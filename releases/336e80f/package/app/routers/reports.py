import asyncio
import logging
from calendar import monthrange
from datetime import date, datetime, timedelta

from aiogram import F, Router, html
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.enums import ExportMode, InspectionWorkType, UserRole
from app.errors import DomainError, EmptyExport
from app.exporting.xlsx import build_inspection_workbook
from app.keyboards import (
    BTN_EXPORT,
    BTN_MY,
    BTN_REPORT,
    MAIN_MENU_BUTTONS,
    ExpertFilterCallback,
    ExpertPageCallback,
    ExportModeCallback,
    ExportRetryCallback,
    MyMonthCallback,
    PeriodCallback,
    ReportActionCallback,
    custom_period_keyboard,
    expert_filter_keyboard,
    export_mode_keyboard,
    failed_exports_keyboard,
    main_menu,
    my_inspection_actions_keyboard,
    my_inspections_keyboard,
    my_month_summary_keyboard,
    period_keyboard,
    retry_export_keyboard,
)
from app.models import User
from app.presentation import format_amount, format_period, inspection_count, status_icon
from app.routers.inspections import _display_status_name, start_saved_inspection_edit
from app.routers.utils import require_active
from app.services.exports import ExportService
from app.services.inspections import InspectionService
from app.services.reports import ReportService
from app.services.users import UserService
from app.states import ReportStates
from app.validators import parse_date_range

router = Router(name="reports")
logger = logging.getLogger(__name__)
EXPERT_PAGE_SIZE = 20
MY_MONTH_PAGE_SIZE = 5


def _roles_for_purpose(purpose: str) -> set[UserRole]:
    if purpose == "export":
        return {UserRole.EXPERT, UserRole.SPECIALIST, UserRole.ADMIN}
    return {UserRole.SPECIALIST, UserRole.ADMIN}


def _roles_for_employee_filter(purpose: str) -> set[UserRole]:
    return (
        {UserRole.ADMIN}
        if purpose == "export"
        else {
            UserRole.SPECIALIST,
            UserRole.ADMIN,
        }
    )


def _month_period(current: date) -> tuple[date, date]:
    return current.replace(day=1), current.replace(day=monthrange(current.year, current.month)[1])


def _previous_month_period(current: date) -> tuple[date, date]:
    previous_end = current.replace(day=1) - timedelta(days=1)
    return previous_end.replace(day=1), previous_end


def _inspection_unit_count(rows) -> int:
    """Count inspections, not the per-order rows produced for Excel."""
    return len({row.inspection_id for row in rows})


def _period_for_choice(choice: str, today: date) -> tuple[date, date] | None:
    if choice == "today":
        return today, today
    if choice == "current":
        return _month_period(today)
    if choice == "previous":
        return _previous_month_period(today)
    return None


async def _show_expert_filter(
    event: Message | CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    purpose: str,
    *,
    page: int = 0,
) -> None:
    results = await UserService(session).list_for_reports(
        limit=EXPERT_PAGE_SIZE + 1,
        offset=page * EXPERT_PAGE_SIZE,
    )
    experts = results[:EXPERT_PAGE_SIZE]
    message = event.message if isinstance(event, CallbackQuery) else event
    data = await state.get_data()
    start = date.fromisoformat(data["period_from"])
    end = date.fromisoformat(data["period_to"])
    await message.answer(
        "👤 <b>Фильтр по сотруднику</b>\n\n"
        f"🗓 Период: <b>{format_period(start, end)}</b>\n"
        f"📄 Страница: {page + 1}\n\n"
        "Выберите одного сотрудника или включите всех:",
        reply_markup=expert_filter_keyboard(
            purpose,
            experts,
            page=page,
            has_next=len(results) > EXPERT_PAGE_SIZE,
        ),
    )


async def _continue_after_period(
    event: Message | CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    current_user: User,
    settings: Settings,
    purpose: str,
) -> None:
    if purpose == "export" and current_user.role is not UserRole.ADMIN:
        await state.update_data(
            expert_id=current_user.id,
            expert_name=current_user.full_name,
        )
        await _execute_export(
            event,
            state,
            session,
            current_user,
            settings,
            ExportMode.FULL,
            affects_processing=False,
        )
        return
    await _show_expert_filter(event, state, session, purpose)


@router.message(Command("my"))
@router.message(F.text == BTN_MY)
async def my_statistics(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    current_user: User | None,
    settings: Settings,
) -> None:
    if not await require_active(
        message, current_user, {UserRole.EXPERT, UserRole.SPECIALIST, UserRole.ADMIN}
    ):
        return
    await state.clear()
    today = datetime.now(settings.timezone).date()
    start, end = _month_period(today)
    count = await InspectionService(session).count_for_user(current_user.id, start, end)
    title = "Все записи за месяц" if current_user.role is UserRole.ADMIN else "Мой месяц"
    await message.answer(
        f"📊 <b>{title}</b>\n\n"
        f"🗓 {format_period(start, end)}\n"
        f"✅ <b>{inspection_count(count)}</b>\n"
        "Одна смена или одна машина считается одной инспекцией.\n\n"
        + (
            "Так держать — все записи учтены."
            if count
            else "Пока нет инспекций. Добавить первую: /add"
        ),
        # The list covers the full history, so it must stay reachable even when
        # the current month has no inspections.
        reply_markup=my_month_summary_keyboard(),
    )


async def _show_my_inspections(
    event: Message | CallbackQuery,
    session: AsyncSession,
    current_user: User,
    *,
    page: int,
) -> None:
    page = max(page, 0)
    results = await InspectionService(session).list_for_user(
        current_user.id,
        date.min,
        date.max,
        limit=MY_MONTH_PAGE_SIZE + 1,
        offset=page * MY_MONTH_PAGE_SIZE,
    )
    inspections = results[:MY_MONTH_PAGE_SIZE]
    message = event.message if isinstance(event, CallbackQuery) else event
    if not inspections:
        await message.answer(
            "📭 <b>Заказов на этой странице нет</b>",
            reply_markup=my_inspections_keyboard([], page=0, has_next=False),
        )
        return
    lines = [
        "📦 <b>Все заказы</b>" if current_user.role is UserRole.ADMIN else "📦 <b>Мои заказы</b>",
        f"Все сохранённые записи · страница {page + 1}",
        "",
    ]
    for index, inspection in enumerate(inspections, start=page * MY_MONTH_PAGE_SIZE + 1):
        orders = ", ".join(item.order_number_raw for item in inspection.order_numbers)
        lines.append(
            f"{index}. <code>{html.quote(orders)}</code> · {inspection.inspection_date:%d.%m.%Y}"
            + (
                f" · {html.quote(inspection.author.full_name)}"
                if current_user.role is UserRole.ADMIN
                else ""
            )
        )
    lines.extend(["", "Выберите заказ, чтобы посмотреть или изменить запись."])
    await message.answer(
        "\n".join(lines),
        reply_markup=my_inspections_keyboard(
            inspections, page=page, has_next=len(results) > MY_MONTH_PAGE_SIZE
        ),
    )


@router.callback_query(MyMonthCallback.filter())
async def my_month_navigation(
    callback: CallbackQuery,
    callback_data: MyMonthCallback,
    state: FSMContext,
    session: AsyncSession,
    current_user: User | None,
    settings: Settings,
) -> None:
    if not await require_active(
        callback, current_user, {UserRole.EXPERT, UserRole.SPECIALIST, UserRole.ADMIN}
    ):
        return
    if callback_data.action == "close":
        await callback.answer()
        await callback.message.answer(
            "✅ <b>Раздел закрыт</b>", reply_markup=main_menu(current_user)
        )
        return
    if callback_data.action == "list":
        await callback.answer()
        await _show_my_inspections(
            callback,
            session,
            current_user,
            page=callback_data.page,
        )
        return
    try:
        inspection = await InspectionService(session).get_for_user(
            callback_data.inspection_id, current_user.id
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if callback_data.action == "edit":
        await callback.answer()
        await start_saved_inspection_edit(
            callback,
            state,
            inspection,
            is_duplicate=inspection.status.allows_duplicate,
        )
        return
    if callback_data.action != "view":
        await callback.answer("Неизвестное действие", show_alert=True)
        return
    processed = await InspectionService(session).is_processed(inspection.id)
    work_label = (
        "🦺 Работа эксперта"
        if inspection.work_type is InspectionWorkType.EXPERT
        else "📋 Работа специалиста"
    )
    orders = ", ".join(item.order_number_raw for item in inspection.order_numbers)
    changed = (
        inspection.updated_at.astimezone(settings.timezone).strftime("%d.%m.%Y %H:%M")
        if inspection.updated_at
        else "не изменялась"
    )
    suppliers = "\n".join(
        f"🏭 <code>{html.quote(item.order_number_raw)}</code>: "
        f"{html.quote(item.supplier_name_snapshot or '—')}"
        for item in inspection.order_numbers
    )
    display_status = (
        inspection.status.name
        if inspection.status.code is None
        else _display_status_name(inspection.scenario, inspection.work_type)
    )
    if inspection.vehicle_number:
        display_status = f"{display_status} {inspection.vehicle_number}"
    await callback.answer()
    await callback.message.answer(
        "🧾 <b>Инспекция</b>\n\n"
        f"📦 Заказы: <code>{html.quote(orders)}</code>\n"
        f"📅 Дата: <b>{inspection.inspection_date:%d.%m.%Y}</b>\n"
        f"👤 Сотрудник: {html.quote(inspection.author.full_name)}\n"
        f"👥 Вид: {work_label}\n"
        f"{suppliers}\n"
        f"{status_icon(display_status)} Статус: {html.quote(display_status)}\n"
        f"🧭 Проект: {html.quote(inspection.project.name)}\n"
        f"💳 Ставка: <b>{format_amount(inspection.rate)} ₽</b>\n"
        f"💬 Комментарий: {html.quote(inspection.comment or '—')}\n"
        f"✏️ Последнее изменение: {changed}\n"
        f"📤 Обработка: {'выгружено' if processed else 'не выгружено'}",
        reply_markup=my_inspection_actions_keyboard(
            inspection.id, page=callback_data.page, editable=True
        ),
    )


@router.message(Command("report"))
@router.message(F.text == BTN_REPORT)
async def report_start(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    current_user: User | None,
    settings: Settings,
) -> None:
    if not await require_active(message, current_user, {UserRole.SPECIALIST, UserRole.ADMIN}):
        return
    await state.clear()
    today = datetime.now(settings.timezone).date()
    start, end = _month_period(today)
    await state.update_data(
        purpose="report",
        period_from=start.isoformat(),
        period_to=end.isoformat(),
    )
    await message.answer(
        "📈 <b>Общий отчёт</b>\n\n"
        f"По умолчанию: <b>{format_period(start, end)}</b>\n"
        "Выберите период для отчёта:",
        reply_markup=period_keyboard("report"),
    )


@router.message(Command("export"))
@router.message(F.text == BTN_EXPORT)
async def export_start(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    current_user: User | None,
    settings: Settings,
) -> None:
    if not await require_active(
        message,
        current_user,
        {UserRole.EXPERT, UserRole.SPECIALIST, UserRole.ADMIN},
    ):
        return
    await state.clear()
    failed_batches = await ExportService(
        session,
        reservation_minutes=settings.export_reservation_minutes,
    ).list_failed_for_user(current_user.id)
    if failed_batches:
        await message.answer(
            "♻️ <b>Есть незавершённые выгрузки</b>\n\n"
            "Состав файлов сохранён — можно безопасно повторить отправку:",
            reply_markup=failed_exports_keyboard(failed_batches),
        )
    scope_hint = (
        "Можно выбрать сотрудника или выгрузить всех."
        if current_user.role is UserRole.ADMIN
        else "В файл попадут только ваши инспекции."
    )
    await message.answer(
        "📤 <b>Excel-выгрузка</b>\n\n"
        "Файл будет содержать листы АК, СВ и Х5.\n"
        f"{scope_hint}\n\nВыберите период:",
        reply_markup=period_keyboard("export"),
    )


@router.callback_query(PeriodCallback.filter())
async def period_selected(
    callback: CallbackQuery,
    callback_data: PeriodCallback,
    state: FSMContext,
    session: AsyncSession,
    current_user: User | None,
    settings: Settings,
) -> None:
    if callback_data.purpose not in {"report", "export"}:
        await callback.answer("Неизвестное действие", show_alert=True)
        return
    if not await require_active(callback, current_user, _roles_for_purpose(callback_data.purpose)):
        return
    await callback.answer()
    if callback_data.choice == "custom":
        await state.update_data(purpose=callback_data.purpose)
        await state.set_state(ReportStates.custom_period)
        await callback.message.answer(
            "✏️ <b>Произвольный период</b>\n\n"
            "Введите начало и конец через тире.\n"
            "Например: <code>01.07.2026 — 14.07.2026</code>",
            reply_markup=custom_period_keyboard(callback_data.purpose),
        )
        return
    today = datetime.now(settings.timezone).date()
    period = _period_for_choice(callback_data.choice, today)
    if period is None:
        await callback.message.answer("⚠️ Не удалось определить период. Откройте раздел заново.")
        return
    await state.update_data(
        purpose=callback_data.purpose,
        period_from=period[0].isoformat(),
        period_to=period[1].isoformat(),
    )
    await _continue_after_period(
        callback,
        state,
        session,
        current_user,
        settings,
        callback_data.purpose,
    )


@router.message(ReportStates.custom_period, F.text, ~F.text.in_(MAIN_MENU_BUTTONS))
async def custom_period(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    current_user: User | None,
    settings: Settings,
) -> None:
    data = await state.get_data()
    purpose = data.get("purpose", "report")
    if not await require_active(message, current_user, _roles_for_purpose(purpose)):
        return
    try:
        start, end = parse_date_range(message.text, today=datetime.now(settings.timezone).date())
    except DomainError as exc:
        await message.answer(
            f"⚠️ <b>Проверьте период</b>\n\n{html.quote(str(exc))}",
            reply_markup=custom_period_keyboard(purpose),
        )
        return
    await state.update_data(period_from=start.isoformat(), period_to=end.isoformat())
    await _continue_after_period(
        message,
        state,
        session,
        current_user,
        settings,
        purpose,
    )


@router.callback_query(ExpertPageCallback.filter())
async def expert_page_selected(
    callback: CallbackQuery,
    callback_data: ExpertPageCallback,
    state: FSMContext,
    session: AsyncSession,
    current_user: User | None,
) -> None:
    if callback_data.purpose not in {"report", "export"}:
        await callback.answer("Неизвестное действие", show_alert=True)
        return
    if not await require_active(
        callback, current_user, _roles_for_employee_filter(callback_data.purpose)
    ):
        return
    data = await state.get_data()
    if callback_data.purpose != data.get("purpose") or not data.get("period_from"):
        await callback.answer("Фильтр устарел, начните заново", show_alert=True)
        return
    await callback.answer()
    await _show_expert_filter(
        callback,
        state,
        session,
        callback_data.purpose,
        page=max(callback_data.page, 0),
    )


@router.callback_query(ExpertFilterCallback.filter())
async def expert_selected(
    callback: CallbackQuery,
    callback_data: ExpertFilterCallback,
    state: FSMContext,
    session: AsyncSession,
    current_user: User | None,
    settings: Settings,
) -> None:
    if callback_data.purpose not in {"report", "export"}:
        await callback.answer("Неизвестное действие", show_alert=True)
        return
    if not await require_active(
        callback, current_user, _roles_for_employee_filter(callback_data.purpose)
    ):
        return
    data = await state.get_data()
    if not data.get("period_from") or callback_data.purpose != data.get("purpose"):
        await callback.answer("Период устарел, начните заново", show_alert=True)
        return
    expert_id = callback_data.expert_id or None
    expert_name = "Все сотрудники"
    if expert_id is not None:
        try:
            expert_name = (await UserService(session).get(expert_id)).full_name
        except DomainError:
            await callback.answer("Эксперт больше не доступен", show_alert=True)
            return
    await state.update_data(expert_id=expert_id, expert_name=expert_name)
    await callback.answer()
    if callback_data.purpose == "report":
        await _send_report(callback, state, session, current_user)
        return
    start = date.fromisoformat(data["period_from"])
    end = date.fromisoformat(data["period_to"])
    await callback.message.answer(
        "📦 <b>Состав выгрузки</b>\n\n"
        f"🗓 {format_period(start, end)}\n"
        f"👤 {html.quote(expert_name)}\n\n"
        "🆕 <b>Только невыгруженные</b> — строки, ещё не попадавшие в успешную "
        "администраторскую выгрузку.\n"
        "📚 <b>Все за период</b> — полный срез; включённые строки также будут "
        "отмечены выгруженными.",
        reply_markup=export_mode_keyboard(),
    )


async def _send_report(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    current_user: User,
) -> None:
    data = await state.get_data()
    start = date.fromisoformat(data["period_from"])
    end = date.fromisoformat(data["period_to"])
    counts = await ReportService(session).count_by_expert(
        start, end, expert_id=data.get("expert_id")
    )
    if counts:
        lines = [
            f"• {html.quote(item.full_name)} — <b>{inspection_count(item.count)}</b>"
            for item in counts
        ]
        body = "\n".join(lines)
    else:
        body = "За выбранный период инспекций нет."
    total = sum(item.count for item in counts)
    expert_name = html.quote(data.get("expert_name", "Все сотрудники"))
    await state.clear()
    await callback.message.answer(
        "📈 <b>Общий отчёт</b>\n\n"
        f"🗓 {format_period(start, end)}\n"
        f"👤 {expert_name}\n"
        f"✅ Итого: <b>{inspection_count(total)}</b>\n\n"
        f"{body}",
        reply_markup=main_menu(current_user),
    )


@router.callback_query(ExportModeCallback.filter())
async def export_mode_selected(
    callback: CallbackQuery,
    callback_data: ExportModeCallback,
    state: FSMContext,
    session: AsyncSession,
    current_user: User | None,
    settings: Settings,
) -> None:
    if not await require_active(callback, current_user, {UserRole.ADMIN}):
        return
    data = await state.get_data()
    if not data.get("period_from") or not data.get("period_to"):
        await callback.answer(
            "Эта кнопка уже неактуальна. Откройте выгрузку заново.", show_alert=True
        )
        return
    await callback.answer()
    await _execute_export(
        callback,
        state,
        session,
        current_user,
        settings,
        ExportMode(callback_data.mode),
        affects_processing=True,
    )


@router.callback_query(ExportRetryCallback.filter())
async def export_retry(
    callback: CallbackQuery,
    callback_data: ExportRetryCallback,
    state: FSMContext,
    session: AsyncSession,
    current_user: User | None,
    settings: Settings,
) -> None:
    if not await require_active(
        callback,
        current_user,
        {UserRole.EXPERT, UserRole.SPECIALIST, UserRole.ADMIN},
    ):
        return
    service = ExportService(
        session,
        reservation_minutes=settings.export_reservation_minutes,
    )
    try:
        batch = await service.prepare_retry(callback_data.batch_id, current_user.id)
        await session.commit()
    except DomainError as exc:
        await session.rollback()
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("Повторяю выгрузку")
    await _deliver_export(
        callback,
        state,
        session,
        current_user,
        settings,
        batch.id,
    )


async def _execute_export(
    event: Message | CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    current_user: User,
    settings: Settings,
    mode: ExportMode,
    *,
    affects_processing: bool,
) -> None:
    data = await state.get_data()
    message = event.message if isinstance(event, CallbackQuery) else event
    start = date.fromisoformat(data["period_from"])
    end = date.fromisoformat(data["period_to"])
    requester_id = current_user.id
    return_menu = main_menu(current_user)
    service = ExportService(session, reservation_minutes=settings.export_reservation_minutes)
    try:
        batch = await service.reserve(
            requested_by_id=requester_id,
            period_from=start,
            period_to=end,
            expert_id=data.get("expert_id"),
            mode=mode,
            affects_processing=affects_processing,
        )
        await session.commit()
    except EmptyExport as exc:
        await session.rollback()
        await state.clear()
        await message.answer(
            f"📭 <b>Нет данных для выгрузки</b>\n\n{html.quote(str(exc))}",
            reply_markup=return_menu,
        )
        return
    except DomainError as exc:
        await session.rollback()
        await state.clear()
        await message.answer(
            f"🚫 <b>Выгрузка недоступна</b>\n\n{html.quote(str(exc))}",
            reply_markup=return_menu,
        )
        return
    await _deliver_export(
        event,
        state,
        session,
        current_user,
        settings,
        batch.id,
    )


async def _deliver_export(
    event: Message | CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    current_user: User,
    settings: Settings,
    batch_id: int,
) -> None:
    service = ExportService(
        session,
        reservation_minutes=settings.export_reservation_minutes,
    )
    message = event.message if isinstance(event, CallbackQuery) else event
    progress = await message.answer(
        "⏳ <b>Готовлю Excel</b>\n\nСобираю строки и применяю форматирование…"
    )
    try:
        batch, rows = await service.load_rows(batch_id)
        inspection_units = _inspection_unit_count(rows)
        content = await asyncio.to_thread(
            build_inspection_workbook, rows, timezone=settings.timezone
        )
        filename = (
            f"Инспекции_{batch.period_from:%Y-%m-%d}_{batch.period_to:%Y-%m-%d}_"
            f"{datetime.now(settings.timezone):%Y%m%d_%H%M}.xlsx"
        )
        sent = await message.answer_document(
            BufferedInputFile(content, filename=filename),
            caption=(
                "📤 <b>Excel готов</b>\n"
                f"🗓 {format_period(batch.period_from, batch.period_to)}\n"
                f"🧾 Пакет #{batch.id} · {inspection_count(inspection_units)}"
            ),
        )
        await service.mark_sent(
            batch_id,
            telegram_chat_id=sent.chat.id,
            telegram_message_id=sent.message_id,
        )
        await session.commit()
        await progress.edit_text(
            "✅ <b>Выгрузка завершена</b>\n\n"
            f"В файл добавлено: <b>{inspection_count(inspection_units)}</b>."
        )
    except Exception as exc:
        await session.rollback()
        await service.mark_failed(batch_id, str(exc))
        await session.commit()
        logger.exception("Failed to generate or send export batch %s", batch_id)
        await progress.edit_text(
            "⚠️ <b>Не удалось отправить файл</b>\n\n"
            "Состав пакета сохранён, а строки освобождены. Можно безопасно повторить отправку.",
            reply_markup=retry_export_keyboard(batch_id),
        )
    finally:
        await state.clear()


@router.callback_query(ReportActionCallback.filter())
async def report_navigation(
    callback: CallbackQuery,
    callback_data: ReportActionCallback,
    state: FSMContext,
    session: AsyncSession,
    current_user: User | None,
) -> None:
    if callback_data.purpose not in {"report", "export"}:
        await callback.answer("Неизвестное действие", show_alert=True)
        return
    required_roles = (
        _roles_for_employee_filter(callback_data.purpose)
        if callback_data.action == "experts"
        else _roles_for_purpose(callback_data.purpose)
    )
    if not await require_active(callback, current_user, required_roles):
        return
    await callback.answer()
    if callback_data.action == "cancel":
        await state.clear()
        await callback.message.answer(
            "✖️ <b>Раздел закрыт</b>", reply_markup=main_menu(current_user)
        )
        return
    if callback_data.action == "period":
        await state.update_data(purpose=callback_data.purpose)
        await state.set_state(None)
        title = (
            "📈 <b>Выберите период отчёта</b>"
            if callback_data.purpose == "report"
            else "📤 <b>Выберите период выгрузки</b>"
        )
        await callback.message.answer(title, reply_markup=period_keyboard(callback_data.purpose))
        return
    if callback_data.action == "experts":
        data = await state.get_data()
        if not data.get("period_from"):
            await callback.message.answer(
                "Период больше неактуален. Выберите его заново.",
                reply_markup=period_keyboard(callback_data.purpose),
            )
            return
        await _show_expert_filter(callback, state, session, callback_data.purpose)
