from datetime import datetime, timedelta
from decimal import Decimal

from aiogram import Bot, F, Router, html
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.enums import (
    InspectionScenario,
    InspectionWorkType,
    ProjectCode,
    ReconciliationState,
    UserRole,
)
from app.errors import DomainError, DuplicateOrderNotAllowed
from app.keyboards import (
    BTN_ADD,
    MAIN_MENU_BUTTONS,
    BatchCallback,
    InspectionActionCallback,
    InspectionChoiceCallback,
    after_save_keyboard,
    comment_keyboard,
    confirmation_keyboard,
    date_keyboard,
    main_menu,
    navigation_keyboard,
    project_keyboard,
    related_orders_keyboard,
    status_keyboard,
    supplier_results_keyboard,
    work_type_keyboard,
)
from app.models import User
from app.presentation import format_amount, status_icon
from app.routers.utils import require_active
from app.services.directories import DirectoryService
from app.services.inspections import (
    InspectionInput,
    InspectionService,
    InspectionUpdateInput,
    OrderNumberInput,
)
from app.services.reconciliation import ReconciliationService
from app.states import InspectionStates
from app.validators import (
    clean_comment,
    parse_inspection_date,
    parse_money,
    validate_order_number,
)

router = Router(name="inspections")

EXPERT_GENERAL_SCENARIOS = {
    InspectionScenario.INSPECTION_STOP,
    InspectionScenario.CONTROL_SHIPMENT,
    InspectionScenario.SAME_VEHICLE,
    InspectionScenario.IDLE_TRIP,
    InspectionScenario.NEXT,
}
EXPERT_X5_SELF_PICKUP_SCENARIOS = {
    InspectionScenario.CONTROL_SHIPMENT,
    InspectionScenario.SHIFT,
    InspectionScenario.SAME_VEHICLE,
    InspectionScenario.IDLE_TRIP,
}
EXPERT_WATERMELON_SCENARIOS = {
    InspectionScenario.SHIFT,
    InspectionScenario.NEXT,
}
DUPLICATE_SCENARIOS = {
    InspectionScenario.COMMISSION,
    InspectionScenario.REPEAT,
}


def _display_status_name(scenario: InspectionScenario, work_type: InspectionWorkType) -> str:
    if scenario is InspectionScenario.NEXT:
        return "далее"
    if scenario is InspectionScenario.SAME_VEHICLE:
        return (
            "отгружены в одну авто"
            if work_type is InspectionWorkType.SPECIALIST
            else "отгружались в одной авто"
        )
    return {
        InspectionScenario.SHIFT: "добавить смену",
        InspectionScenario.CONTROL_SHIPMENT: "контроль отгрузки",
        InspectionScenario.INSPECTION_STOP: "осмотр / стоп-отгрузка",
        InspectionScenario.IDLE_TRIP: "холостой выезд",
        InspectionScenario.COMMISSION: "комиссионная инспекция",
        InspectionScenario.REPEAT: "повторная инспекция",
    }[scenario]


def _allowed_scenarios(data: dict) -> set[InspectionScenario]:
    if data.get("is_duplicate"):
        return DUPLICATE_SCENARIOS
    work_type = InspectionWorkType(data["work_type"])
    if work_type is InspectionWorkType.SPECIALIST:
        return {InspectionScenario.NEXT, InspectionScenario.SAME_VEHICLE}
    try:
        project_code = ProjectCode(data.get("project_code"))
    except (TypeError, ValueError):
        return EXPERT_GENERAL_SCENARIOS
    if project_code is ProjectCode.X5_SELF_PICKUP:
        return EXPERT_X5_SELF_PICKUP_SCENARIOS
    if project_code is ProjectCode.THUNDER_WATERMELONS:
        return EXPERT_WATERMELON_SCENARIOS
    return EXPERT_GENERAL_SCENARIOS


def _step(data: dict, number: int) -> str:
    return f"Шаг {number} из 7"


async def _ensure_work_type(
    state: FSMContext,
    current_user: User | None,
    data: dict | None = None,
) -> InspectionWorkType:
    current_data = data if data is not None else await state.get_data()
    stored = current_data.get("work_type")
    if stored:
        return InspectionWorkType(stored)
    if current_user is None:
        raise DomainError("Не удалось определить вид работы. Начните ввод заново: /add")
    work_type = InspectionService.work_type_for_user(current_user)
    await state.update_data(work_type=work_type.value)
    return work_type


async def _initialize_flow(
    state: FSMContext,
    user: User,
    *,
    work_type: InspectionWorkType | None = None,
) -> bool:
    await state.clear()
    if user.role is UserRole.ADMIN and work_type is None:
        await state.set_state(InspectionStates.work_type)
        return False
    selected = work_type or InspectionService.work_type_for_user(user)
    await state.update_data(
        work_type=selected.value,
        related_orders=[],
        scenario=None,
        vehicle_number=None,
    )
    return True


async def _prompt_work_type(event: Message | CallbackQuery) -> None:
    message = event.message if isinstance(event, CallbackQuery) else event
    await message.answer(
        "👥 <b>Вид работы</b>\n\n"
        "Выберите, какую работу вы сейчас фиксируете. Это определяет правила проверки дублей.",
        reply_markup=work_type_keyboard(),
    )


async def _prompt_date(event: Message | CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(InspectionStates.date)
    message = event.message if isinstance(event, CallbackQuery) else event
    await message.answer(
        f"📅 <b>{_step(data, 1)} · Дата инспекции</b>\n\n"
        "Введите дату в формате <code>ДД.ММ.ГГГГ</code> или выберите быстрый вариант.",
        reply_markup=date_keyboard(include_back=bool(data.get("editing"))),
    )


async def _prompt_order(event: Message | CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(InspectionStates.order)
    message = event.message if isinstance(event, CallbackQuery) else event
    work_type = InspectionWorkType(data["work_type"])
    step = 3 if work_type is InspectionWorkType.EXPERT else 2
    back_target = (
        "confirm"
        if data.get("editing")
        else ("project" if work_type is InspectionWorkType.EXPERT else "date")
    )
    await message.answer(
        f"📦 <b>{_step(data, step)} · Номер заказа</b>\n\n"
        "Только латинские буквы и цифры, без пробелов.\n"
        "Например: <code>YUG23000Y8680839</code>\n\n"
        "Поставщика бот определит автоматически по загруженному файлу.",
        reply_markup=navigation_keyboard(back_target),
    )


async def _prompt_supplier(event: Message | CallbackQuery, state: FSMContext) -> None:
    message = event.message if isinstance(event, CallbackQuery) else event
    await message.answer(
        "ℹ️ Ручной выбор поставщика больше не используется. "
        "Он определяется автоматически по файлу сверки."
    )


async def _prompt_status(
    event: Message | CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    data = await state.get_data()
    allowed = _allowed_scenarios(data)
    statuses = [
        status
        for status in await DirectoryService(session).list_statuses()
        if status.code in allowed
    ]
    work_type = InspectionWorkType(data["work_type"])
    labels = {
        status.id: _display_status_name(status.code, work_type)
        for status in statuses
        if status.code is not None
    }
    await state.set_state(InspectionStates.status)
    message = event.message if isinstance(event, CallbackQuery) else event
    duplicate_hint = ""
    if data.get("is_duplicate"):
        duplicate_hint = (
            "\n⚠️ <b>Этот номер уже есть в данном виде работы.</b> "
            "Доступны только статусы, разрешающие повтор.\n"
        )
    await message.answer(
        f"📌 <b>{_step(data, 3 if work_type is InspectionWorkType.SPECIALIST else 4)} · "
        f"Статус</b>\n{duplicate_hint}\nВыберите результат инспекции:",
        reply_markup=status_keyboard(
            statuses,
            back_target="confirm" if data.get("editing") else "order",
            labels=labels,
        ),
    )


async def _prompt_project(
    event: Message | CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    data = await state.get_data()
    projects = [
        project
        for project in await DirectoryService(session).list_projects()
        if project.code is not None
    ]
    await state.set_state(InspectionStates.project)
    message = event.message if isinstance(event, CallbackQuery) else event
    work_type = InspectionWorkType(data["work_type"])
    back_target = (
        "confirm"
        if data.get("editing")
        else (
            "date"
            if work_type is InspectionWorkType.EXPERT
            else ("related" if data.get("supports_related_orders") else "status")
        )
    )
    step = 2 if work_type is InspectionWorkType.EXPERT else 4
    await message.answer(
        f"🧭 <b>{_step(data, step)} · Проект</b>\n\nВыберите проект:",
        reply_markup=project_keyboard(projects, back_target=back_target),
    )


async def _prompt_related_orders(event: Message | CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(InspectionStates.related_order)
    related = list(data.get("related_orders", []))
    lines = [
        "🔗 <b>Заказы в одной отгрузке</b>",
        "",
        f"Основной: <code>{html.quote(data['order_number_raw'])}</code>",
    ]
    if related:
        lines.append("Дополнительные:")
        lines.extend(
            f"{index}. <code>{html.quote(order['raw'])}</code>"
            for index, order in enumerate(related, start=1)
        )
    else:
        lines.append("Дополнительных заказов пока нет.")
    lines.extend(
        [
            "",
            "Отправьте номер сообщением или нажмите «Готово». "
            "Каждый заказ попадёт в отдельную строку Excel, а ставка — только в первую.",
        ]
    )
    message = event.message if isinstance(event, CallbackQuery) else event
    await message.answer("\n".join(lines), reply_markup=related_orders_keyboard(related))


async def _prompt_rate(event: Message | CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(InspectionStates.rate)
    message = event.message if isinstance(event, CallbackQuery) else event
    await message.answer(
        f"💳 <b>{_step(data, 5)} · Ставка</b>\n\n"
        "Введите сумму за инспекцию. Можно использовать точку или запятую.\n"
        "Например: <code>1500</code> или <code>1500,50</code>",
        reply_markup=navigation_keyboard(
            "confirm"
            if data.get("editing")
            else (
                "project"
                if InspectionWorkType(data["work_type"]) is InspectionWorkType.SPECIALIST
                else "status"
            )
        ),
    )


async def _prompt_comment(event: Message | CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(InspectionStates.comment)
    message = event.message if isinstance(event, CallbackQuery) else event
    if data.get("editing"):
        back_target = "confirm"
    else:
        back_target = "rate"
    await message.answer(
        f"💬 <b>{_step(data, 6)} · Комментарий</b>\n\n"
        "Добавьте важную деталь или оставьте поле пустым кнопкой ниже.",
        reply_markup=comment_keyboard(back_target=back_target),
    )


async def _show_confirmation(event: Message | CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(InspectionStates.confirm)
    comment = data.get("comment") or "—"
    related = list(data.get("related_orders", []))
    related_line = ""
    if related:
        values = ", ".join(html.quote(order["raw"]) for order in related)
        related_line = f"🔗 Вместе с заказами: <code>{values}</code>\n"
    primary_supplier = data.get("primary_supplier_name") or "—"
    supplier_lines = [f"🏭 {html.quote(data['order_number_raw'])}: {html.quote(primary_supplier)}"]
    supplier_lines.extend(
        f"🏭 {html.quote(order['raw'])}: {html.quote(order.get('supplier_name') or '—')}"
        for order in related
    )
    vehicle_line = (
        f"🚚 Номер авто: <b>{html.quote(data['vehicle_number'])}</b>\n"
        if data.get("vehicle_number")
        else ""
    )
    summary = (
        "✅ <b>Шаг 7 из 7 · Проверка перед сохранением</b>\n\n"
        f"📅 Дата: <b>{data['inspection_date_display']}</b>\n"
        f"📦 Заказ: <code>{html.quote(data['order_number_raw'])}</code>\n"
        f"{related_line}" + "\n".join(supplier_lines) + "\n"
        f"{status_icon(data['status_name'])} Статус: {html.quote(data['status_name'])}\n"
        f"{vehicle_line}"
        f"🧭 Проект: {html.quote(data['project_name'])}\n"
        f"💳 Ставка: <b>{format_amount(data['rate'])} ₽</b>\n"
        f"💬 Комментарий: {html.quote(comment)}\n\n"
        "Если всё верно — сохраните. Любое поле можно исправить."
    )
    message = event.message if isinstance(event, CallbackQuery) else event
    await message.answer(
        summary,
        reply_markup=confirmation_keyboard(
            supports_related_orders=bool(data.get("supports_related_orders")),
            has_vehicle_number=bool(data.get("vehicle_number")),
            allow_project_edit=not (
                InspectionWorkType(data["work_type"]) is InspectionWorkType.SPECIALIST
                and bool(data.get("is_duplicate"))
            ),
        ),
    )


async def start_saved_inspection_edit(
    event: Message | CallbackQuery,
    state: FSMContext,
    inspection,
    *,
    is_duplicate: bool,
) -> None:
    orders = list(inspection.order_numbers)
    related = [
        {
            "raw": item.order_number_raw,
            "normalized": item.order_number_normalized,
            "supplier_name": item.supplier_name_snapshot,
            "reconciliation_state": item.reconciliation_state.value,
            "reference_upload_id": item.reference_upload_id,
        }
        for item in orders[1:]
    ]
    primary = orders[0] if orders else None
    display_status = (
        inspection.status.name
        if inspection.status.code is None
        else _display_status_name(inspection.scenario, inspection.work_type)
    )
    await state.clear()
    await state.update_data(
        editing_saved_id=inspection.id,
        work_type=inspection.work_type.value,
        inspection_date=inspection.inspection_date.isoformat(),
        inspection_date_display=inspection.inspection_date.strftime("%d.%m.%Y"),
        order_number_raw=inspection.order_number_raw,
        order_number_normalized=inspection.order_number_normalized,
        related_orders=related,
        # In V2 the supplier belongs to each order snapshot, not to the inspection.
        # Dropping the optional legacy link also keeps inactive suppliers from
        # blocking corrections of historical records.
        supplier_id=None,
        primary_supplier_name=(
            primary.supplier_name_snapshot
            if primary is not None
            else (inspection.supplier.name if inspection.supplier else None)
        ),
        primary_reconciliation_state=(
            primary.reconciliation_state.value
            if primary is not None
            else ReconciliationState.LEGACY.value
        ),
        primary_reference_upload_id=(primary.reference_upload_id if primary else None),
        status_id=inspection.status_id,
        status_name=display_status,
        status_allows_duplicate=inspection.status.allows_duplicate,
        supports_related_orders=inspection.scenario
        in {
            InspectionScenario.SAME_VEHICLE,
            InspectionScenario.SHIFT,
        },
        scenario=inspection.scenario.value,
        vehicle_number=inspection.vehicle_number,
        project_id=inspection.project_id,
        project_name=inspection.project.name,
        project_code=inspection.project.code.value if inspection.project.code else None,
        rate=f"{inspection.rate:.2f}",
        comment=inspection.comment,
        is_duplicate=is_duplicate,
        editing=None,
    )
    await _show_confirmation(event, state)


async def _finish_or_continue(
    event: Message | CallbackQuery,
    state: FSMContext,
    next_prompt,
) -> None:
    data = await state.get_data()
    if data.get("editing"):
        await state.update_data(editing=None)
        await _show_confirmation(event, state)
    else:
        await next_prompt()


async def _continue_after_date(
    event: Message | CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    data = await state.get_data()
    if InspectionWorkType(data["work_type"]) is InspectionWorkType.EXPERT:
        await _prompt_project(event, state, session)
    else:
        await _prompt_order(event, state)


@router.message(Command("add"))
@router.message(F.text == BTN_ADD)
async def add_inspection(message: Message, state: FSMContext, current_user: User | None) -> None:
    if not await require_active(
        message, current_user, {UserRole.EXPERT, UserRole.SPECIALIST, UserRole.ADMIN}
    ):
        return
    if await _initialize_flow(state, current_user):
        await _prompt_date(message, state)
    else:
        await _prompt_work_type(message)


@router.callback_query(BatchCallback.filter(F.action == "new"))
async def batch_new(callback: CallbackQuery, state: FSMContext, current_user: User | None) -> None:
    if not await require_active(
        callback, current_user, {UserRole.EXPERT, UserRole.SPECIALIST, UserRole.ADMIN}
    ):
        return
    await callback.answer()
    if await _initialize_flow(state, current_user):
        await _prompt_date(callback, state)
    else:
        await _prompt_work_type(callback)


@router.callback_query(
    InspectionActionCallback.filter(F.action.startswith("work_")),
    InspectionStates.work_type,
)
async def inspection_work_type_choice(
    callback: CallbackQuery,
    callback_data: InspectionActionCallback,
    state: FSMContext,
    current_user: User | None,
) -> None:
    if not await require_active(callback, current_user, {UserRole.ADMIN}):
        return
    try:
        work_type = InspectionWorkType(callback_data.action.removeprefix("work_"))
    except ValueError:
        await callback.answer("Неизвестный вид работы", show_alert=True)
        return
    await _initialize_flow(state, current_user, work_type=work_type)
    await callback.answer()
    await _prompt_date(callback, state)


@router.callback_query(BatchCallback.filter(F.action == "same"))
async def batch_same(
    callback: CallbackQuery,
    callback_data: BatchCallback,
    state: FSMContext,
    session: AsyncSession,
    current_user: User | None,
) -> None:
    if not await require_active(
        callback, current_user, {UserRole.EXPERT, UserRole.SPECIALIST, UserRole.ADMIN}
    ):
        return
    inspection_date = datetime.fromisoformat(callback_data.inspection_date).date()
    work_type = (
        InspectionWorkType(callback_data.work_type) if callback_data.work_type != "x" else None
    )
    ready = await _initialize_flow(state, current_user, work_type=work_type)
    if not ready:
        await callback.answer()
        await _prompt_work_type(callback)
        return
    await state.update_data(
        inspection_date=inspection_date.isoformat(),
        inspection_date_display=inspection_date.strftime("%d.%m.%Y"),
    )
    await callback.answer("Поставщик теперь определяется автоматически")
    await _continue_after_date(callback, state, session)


@router.message(InspectionStates.date, F.text, ~F.text.in_(MAIN_MENU_BUTTONS))
async def inspection_date_text(
    message: Message,
    state: FSMContext,
    settings: Settings,
    session: AsyncSession,
) -> None:
    try:
        parsed = parse_inspection_date(message.text, today=datetime.now(settings.timezone).date())
    except DomainError as exc:
        await message.answer(
            f"⚠️ <b>Проверьте дату</b>\n\n{html.quote(str(exc))}",
            reply_markup=date_keyboard(),
        )
        return
    await state.update_data(
        inspection_date=parsed.isoformat(), inspection_date_display=parsed.strftime("%d.%m.%Y")
    )
    await _finish_or_continue(message, state, lambda: _continue_after_date(message, state, session))


@router.callback_query(
    InspectionActionCallback.filter(F.action.in_({"date_today", "date_yesterday"})),
    InspectionStates.date,
)
async def inspection_date_shortcut(
    callback: CallbackQuery,
    callback_data: InspectionActionCallback,
    state: FSMContext,
    settings: Settings,
    session: AsyncSession,
) -> None:
    current = datetime.now(settings.timezone).date()
    parsed = current if callback_data.action == "date_today" else current - timedelta(days=1)
    await state.update_data(
        inspection_date=parsed.isoformat(), inspection_date_display=parsed.strftime("%d.%m.%Y")
    )
    await callback.answer()
    await _finish_or_continue(
        callback, state, lambda: _continue_after_date(callback, state, session)
    )


@router.message(InspectionStates.order, F.text, ~F.text.in_(MAIN_MENU_BUTTONS))
async def inspection_order(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    current_user: User | None = None,
) -> None:
    try:
        raw, normalized = validate_order_number(message.text)
    except DomainError as exc:
        await message.answer(
            f"⚠️ <b>Проверьте номер заказа</b>\n\n{html.quote(str(exc))}",
            reply_markup=navigation_keyboard("date"),
        )
        return
    data = await state.get_data()
    if normalized in {order["normalized"] for order in data.get("related_orders", [])}:
        await message.answer(
            "⚠️ Этот номер уже указан среди дополнительных заказов. Сначала удалите его из списка."
        )
        return
    work_type = await _ensure_work_type(state, current_user, data)
    number_changed = normalized != data.get("order_number_normalized")
    if data.get("editing_saved_id") and not number_changed:
        duplicate = bool(data.get("is_duplicate"))
    else:
        duplicate = await InspectionService(session).order_exists(
            normalized,
            work_type,
            exclude_inspection_id=data.get("editing_saved_id"),
        )
    if data.get("editing_saved_id") and not number_changed:
        supplier_name = data.get("primary_supplier_name")
        reconciliation_state = ReconciliationState(
            data.get(
                "primary_reconciliation_state",
                ReconciliationState.LEGACY.value,
            )
        )
        reference_upload_id = data.get("primary_reference_upload_id")
    else:
        match = await ReconciliationService(session).resolve(normalized)
        supplier_name = match.supplier_name
        reconciliation_state = match.state
        reference_upload_id = match.upload_id
    await state.update_data(
        order_number_raw=raw,
        order_number_normalized=normalized,
        is_duplicate=duplicate,
        primary_supplier_name=supplier_name,
        primary_reconciliation_state=reconciliation_state.value,
        primary_reference_upload_id=reference_upload_id,
    )
    data = await state.get_data()
    show_reconciliation = number_changed or not data.get("editing_saved_id")
    if show_reconciliation and reconciliation_state is not ReconciliationState.FOUND:
        reason = (
            "Файл для сверки ещё не загружен."
            if reconciliation_state is ReconciliationState.NO_FILE
            else "Заказ не найден в текущем файле."
        )
        await message.answer(
            "⚠️ <b>Проверьте корректность внесения заказа</b>\n\n"
            f"{reason} Продолжить ввод можно, поставщик останется пустым."
        )
    elif show_reconciliation and supplier_name:
        await message.answer(f"✅ Заказ найден. Поставщик: <b>{html.quote(supplier_name)}</b>")
    if duplicate:
        await message.answer(
            "⚠️ <b>Найден такой же номер заказа</b>\n\n"
            "В этом виде работы запись можно продолжить только как «повторную» "
            "или «комиссионную» инспекцию."
        )
        if work_type is InspectionWorkType.SPECIALIST:
            project = await InspectionService(session).first_project_for_order(
                normalized, work_type
            )
            if project is not None:
                await state.update_data(
                    project_id=project.id,
                    project_name=project.name,
                    project_code=project.code.value if project.code else None,
                )
    if data.get("editing"):
        await state.update_data(editing="status", status_id=None, status_name=None)
    refreshed = await state.get_data()
    if work_type is InspectionWorkType.EXPERT and not refreshed.get("project_code"):
        await message.answer("ℹ️ Сценарий ввода обновился: сначала выберите проект.")
        await _prompt_project(message, state, session)
        return
    await _prompt_status(message, state, session)


@router.message(InspectionStates.supplier_query, F.text, ~F.text.in_(MAIN_MENU_BUTTONS))
async def inspection_supplier_search(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    await message.answer(
        "ℹ️ Ручной выбор поставщика удалён. Поставщик уже определён по номеру заказа."
    )
    await _prompt_status(message, state, session)


async def _show_supplier_page(
    event: Message | CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
    *,
    query: str,
    page: int,
) -> None:
    limit = settings.supplier_search_limit
    page = max(page, 0)
    results = await DirectoryService(session).search_suppliers(
        query,
        limit=limit + 1,
        offset=page * limit,
    )
    suppliers = results[:limit]
    has_next = len(results) > limit
    await state.update_data(supplier_search_query=query, supplier_search_page=page)
    message = event.message if isinstance(event, CallbackQuery) else event
    if suppliers:
        await message.answer(
            f"🏭 <b>Выберите поставщика</b> · страница {page + 1}",
            reply_markup=supplier_results_keyboard(
                suppliers,
                page=page,
                has_next=has_next,
                back_target="confirm" if (await state.get_data()).get("editing") else "order",
            ),
        )
    else:
        await message.answer(
            "🔎 <b>Совпадений не найдено</b>\n\n"
            "Введите другой фрагмент или добавьте нового поставщика вручную.",
            reply_markup=supplier_results_keyboard(
                [],
                page=page,
                back_target="confirm" if (await state.get_data()).get("editing") else "order",
            ),
        )


@router.callback_query(
    InspectionActionCallback.filter(F.action.startswith("supplier_page_")),
    InspectionStates.supplier_query,
)
async def inspection_supplier_page(
    callback: CallbackQuery,
    callback_data: InspectionActionCallback,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    await callback.answer(
        "Эта кнопка устарела: поставщик определяется автоматически",
        show_alert=True,
    )
    await callback.message.answer(
        "Поставщик берётся из активного файла сверки по номеру заказа. "
        "Вернитесь к номеру заказа и продолжите ввод."
    )


@router.callback_query(
    InspectionActionCallback.filter(F.action == "supplier_manual"),
    InspectionStates.supplier_query,
)
async def inspection_supplier_manual_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Ручной ввод поставщика больше не используется", show_alert=True)
    await callback.message.answer(
        "Поставщик автоматически определяется по загруженному файлу. "
        "Вернитесь к номеру заказа и продолжите ввод."
    )


@router.message(InspectionStates.supplier_manual, F.text, ~F.text.in_(MAIN_MENU_BUTTONS))
async def inspection_supplier_manual(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    await message.answer("ℹ️ Ручной ввод поставщика удалён. Продолжим с выбором статуса.")
    await _prompt_status(message, state, session)


@router.callback_query(
    InspectionChoiceCallback.filter(F.kind == "supplier"),
    InspectionStates.supplier_query,
)
async def inspection_supplier_choice(
    callback: CallbackQuery,
    callback_data: InspectionChoiceCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    await callback.answer("Поставщик теперь определяется автоматически", show_alert=True)
    await _prompt_status(callback, state, session)


@router.callback_query(InspectionChoiceCallback.filter(F.kind == "status"), InspectionStates.status)
async def inspection_status_choice(
    callback: CallbackQuery,
    callback_data: InspectionChoiceCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    status = await DirectoryService(session).get_status(callback_data.entity_id)
    data = await state.get_data()
    if status is None or not status.active or status.code is None:
        await callback.answer("Статус больше не доступен", show_alert=True)
        return
    if status.code not in _allowed_scenarios(data):
        await callback.answer("Этот статус недоступен для выбранного сценария", show_alert=True)
        return
    work_type = InspectionWorkType(data["work_type"])
    await state.update_data(
        status_id=status.id,
        status_name=_display_status_name(status.code, work_type),
        status_allows_duplicate=status.allows_duplicate,
        supports_related_orders=status.supports_related_orders,
        scenario=status.code.value,
        vehicle_number=(
            data.get("vehicle_number") if status.code is InspectionScenario.SAME_VEHICLE else None
        ),
        related_orders=(data.get("related_orders", []) if status.supports_related_orders else []),
    )
    await callback.answer()
    if status.supports_related_orders:
        await _prompt_related_orders(callback, state)
    elif data.get("editing"):
        await state.update_data(editing=None)
        await _show_confirmation(callback, state)
    elif work_type is InspectionWorkType.SPECIALIST and not data.get("project_id"):
        await _prompt_project(callback, state, session)
    else:
        await _prompt_rate(callback, state)


@router.message(InspectionStates.related_order, F.text, ~F.text.in_(MAIN_MENU_BUTTONS))
async def inspection_related_order(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    current_user: User | None = None,
) -> None:
    try:
        raw, normalized = validate_order_number(message.text)
    except DomainError as exc:
        await message.answer(f"⚠️ <b>Проверьте номер заказа</b>\n\n{html.quote(str(exc))}")
        return
    data = await state.get_data()
    if len(data.get("related_orders", [])) >= 9:
        await message.answer("⚠️ В одной инспекции можно указать не более 10 заказов.")
        return
    existing = {
        data["order_number_normalized"],
        *(order["normalized"] for order in data.get("related_orders", [])),
    }
    if normalized in existing:
        await message.answer("⚠️ Этот номер уже добавлен в текущую инспекцию.")
        return
    duplicate = await InspectionService(session).order_exists(
        normalized,
        await _ensure_work_type(state, current_user, data),
        exclude_inspection_id=data.get("editing_saved_id"),
    )
    if duplicate and not data.get("status_allows_duplicate"):
        await message.answer(
            "⚠️ Этот номер уже есть в данном виде работы. Для текущего статуса добавить его нельзя."
        )
        return
    match = await ReconciliationService(session).resolve(normalized)
    related = [
        *data.get("related_orders", []),
        {
            "raw": raw,
            "normalized": normalized,
            "supplier_name": match.supplier_name,
            "reconciliation_state": match.state.value,
            "reference_upload_id": match.upload_id,
        },
    ]
    await state.update_data(related_orders=related)
    if match.state is not ReconciliationState.FOUND:
        await message.answer(
            "⚠️ <b>Проверьте корректность внесения заказа</b>\n\n"
            "Номер не найден в активном файле либо файл ещё не загружен. "
            "Он добавлен, поставщик останется пустым."
        )
    await _prompt_related_orders(message, state)


@router.callback_query(
    InspectionActionCallback.filter(F.action == "related_add"),
    InspectionStates.related_order,
)
async def inspection_related_add(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        "➕ Отправьте дополнительный номер заказа: только латинские буквы и цифры, без пробелов."
    )


@router.callback_query(
    InspectionActionCallback.filter(F.action.startswith("related_remove_")),
    InspectionStates.related_order,
)
async def inspection_related_remove(
    callback: CallbackQuery,
    callback_data: InspectionActionCallback,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    related = list(data.get("related_orders", []))
    try:
        index = int(callback_data.action.removeprefix("related_remove_"))
        removed = related.pop(index)
    except (ValueError, IndexError):
        await callback.answer("Список уже изменился", show_alert=True)
        return
    await state.update_data(related_orders=related)
    await callback.answer(f"Убран {removed['raw']}")
    await _prompt_related_orders(callback, state)


@router.callback_query(
    InspectionActionCallback.filter(F.action == "related_done"),
    InspectionStates.related_order,
)
async def inspection_related_done(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    data = await state.get_data()
    scenario = InspectionScenario(data["scenario"])
    if scenario is InspectionScenario.SAME_VEHICLE and not data.get("related_orders"):
        await callback.answer(
            "Для одной машины добавьте хотя бы один дополнительный заказ",
            show_alert=True,
        )
        return
    await callback.answer()
    if (
        scenario is InspectionScenario.SAME_VEHICLE
        and data.get("project_code") == ProjectCode.X5_SELF_PICKUP.value
    ):
        await _prompt_vehicle_number(callback, state)
    elif data.get("editing"):
        await state.update_data(editing=None)
        await _show_confirmation(callback, state)
    elif InspectionWorkType(data["work_type"]) is InspectionWorkType.SPECIALIST:
        await _prompt_project(callback, state, session)
    else:
        await _prompt_rate(callback, state)


@router.callback_query(
    InspectionChoiceCallback.filter(F.kind == "project"), InspectionStates.project
)
async def inspection_project_choice(
    callback: CallbackQuery,
    callback_data: InspectionChoiceCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    data = await state.get_data()
    if InspectionWorkType(data["work_type"]) is InspectionWorkType.SPECIALIST and data.get(
        "is_duplicate"
    ):
        await callback.answer(
            "Для повтора проект берётся из первой записи этого заказа",
            show_alert=True,
        )
        if data.get("editing"):
            await state.update_data(editing=None)
            await _show_confirmation(callback, state)
        return
    project = await DirectoryService(session).get_project(callback_data.entity_id)
    if project is None or not project.active:
        await callback.answer("Проект больше не доступен", show_alert=True)
        return
    await state.update_data(project_id=project.id, project_name=project.name)
    await callback.answer()
    await state.update_data(project_code=project.code.value if project.code else None)
    data = await state.get_data()
    if data.get("editing"):
        if InspectionWorkType(data["work_type"]) is InspectionWorkType.EXPERT:
            await state.update_data(editing="status", status_id=None, status_name=None)
            await _prompt_status(callback, state, session)
        else:
            await state.update_data(editing=None)
            await _show_confirmation(callback, state)
    elif InspectionWorkType(data["work_type"]) is InspectionWorkType.EXPERT:
        if data.get("order_number_normalized"):
            await _prompt_status(callback, state, session)
        else:
            await _prompt_order(callback, state)
    else:
        await _prompt_rate(callback, state)


async def _prompt_vehicle_number(event: Message | CallbackQuery, state: FSMContext) -> None:
    await state.set_state(InspectionStates.vehicle_number)
    message = event.message if isinstance(event, CallbackQuery) else event
    await message.answer(
        "🚚 <b>Номер автомобиля</b>\n\nВведите номер автомобиля для совместной отгрузки.",
        reply_markup=navigation_keyboard("related"),
    )


@router.message(InspectionStates.vehicle_number, F.text, ~F.text.in_(MAIN_MENU_BUTTONS))
async def inspection_vehicle_number(message: Message, state: FSMContext) -> None:
    value = " ".join(message.text.split())
    if not value or len(value) > 64:
        await message.answer("⚠️ Введите номер автомобиля длиной до 64 символов.")
        return
    await state.update_data(vehicle_number=value)
    data = await state.get_data()
    if data.get("editing"):
        await state.update_data(editing=None)
        await _show_confirmation(message, state)
    else:
        await _prompt_rate(message, state)


async def _save_money_field(
    message: Message,
    state: FSMContext,
    field: str,
    next_prompt,
) -> None:
    try:
        amount = parse_money(message.text)
    except DomainError as exc:
        await message.answer(f"⚠️ <b>Проверьте сумму</b>\n\n{html.quote(str(exc))}")
        return
    await state.update_data(**{field: f"{amount:.2f}"})
    await _finish_or_continue(message, state, next_prompt)


@router.message(InspectionStates.rate, F.text, ~F.text.in_(MAIN_MENU_BUTTONS))
async def inspection_rate(message: Message, state: FSMContext) -> None:
    await _save_money_field(message, state, "rate", lambda: _prompt_comment(message, state))


@router.message(InspectionStates.fuel)
async def legacy_inspection_fuel(message: Message, state: FSMContext) -> None:
    """Resume a dialog paused on the GСМ step removed in this release."""
    await message.answer("ℹ️ Поле ГСМ больше не используется. Продолжим с комментарием.")
    await _prompt_comment(message, state)


@router.message(InspectionStates.comment, F.text, ~F.text.in_(MAIN_MENU_BUTTONS))
async def inspection_comment(message: Message, state: FSMContext) -> None:
    try:
        comment = clean_comment(message.text)
    except DomainError as exc:
        await message.answer(f"⚠️ <b>Комментарий не сохранён</b>\n\n{html.quote(str(exc))}")
        return
    await state.update_data(comment=comment)
    await _finish_or_continue(message, state, lambda: _show_confirmation(message, state))


@router.callback_query(
    InspectionActionCallback.filter(F.action == "comment_skip"), InspectionStates.comment
)
async def inspection_comment_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(comment=None)
    await callback.answer()
    await _finish_or_continue(callback, state, lambda: _show_confirmation(callback, state))


@router.callback_query(
    InspectionActionCallback.filter(F.action.startswith("edit_")), InspectionStates.confirm
)
async def inspection_edit(
    callback: CallbackQuery,
    callback_data: InspectionActionCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    field = callback_data.action.removeprefix("edit_")
    data = await state.get_data()
    if (
        field == "project"
        and InspectionWorkType(data["work_type"]) is InspectionWorkType.SPECIALIST
        and data.get("is_duplicate")
    ):
        await callback.answer(
            "Для повтора проект зафиксирован по первой записи заказа",
            show_alert=True,
        )
        await state.update_data(editing=None)
        await _show_confirmation(callback, state)
        return
    await state.update_data(editing=field)
    await callback.answer()
    if field == "date":
        await _prompt_date(callback, state)
    elif field == "order":
        await _prompt_order(callback, state)
    elif field == "supplier":
        await callback.message.answer(
            "ℹ️ Поставщик определяется по номеру заказа и отдельно не редактируется."
        )
        await state.update_data(editing=None)
        await _show_confirmation(callback, state)
    elif field == "status":
        await _prompt_status(callback, state, session)
    elif field == "related":
        await _prompt_related_orders(callback, state)
    elif field == "vehicle":
        await _prompt_vehicle_number(callback, state)
    elif field == "project":
        await _prompt_project(callback, state, session)
    elif field == "rate":
        await _prompt_rate(callback, state)
    elif field == "comment":
        await _prompt_comment(callback, state)


@router.callback_query(InspectionActionCallback.filter(F.action.startswith("back_")))
async def inspection_back(
    callback: CallbackQuery,
    callback_data: InspectionActionCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    target = callback_data.action.removeprefix("back_")
    await callback.answer()
    if target == "confirm":
        await state.update_data(editing=None)
        await _show_confirmation(callback, state)
    elif target == "date":
        await _prompt_date(callback, state)
    elif target == "order":
        await _prompt_order(callback, state)
    elif target == "supplier":
        await _prompt_order(callback, state)
    elif target == "status":
        await _prompt_status(callback, state, session)
    elif target == "related":
        await _prompt_related_orders(callback, state)
    elif target == "project":
        await _prompt_project(callback, state, session)
    elif target == "rate":
        await _prompt_rate(callback, state)
    elif target == "vehicle":
        await _prompt_vehicle_number(callback, state)


@router.callback_query(InspectionActionCallback.filter(F.action == "cancel"))
async def inspection_cancel(
    callback: CallbackQuery, state: FSMContext, current_user: User | None
) -> None:
    was_edit = bool((await state.get_data()).get("editing_saved_id"))
    await state.clear()
    await callback.answer()
    action = "Изменение" if was_edit else "Добавление"
    await callback.message.answer(
        f"✖️ <b>{action} отменено</b>\n\nДанные не сохранены.",
        reply_markup=main_menu(current_user),
    )


@router.callback_query(
    InspectionActionCallback.filter(F.action == "save"), InspectionStates.confirm
)
async def inspection_save(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    current_user: User | None,
    bot: Bot,
    settings: Settings,
) -> None:
    if not await require_active(
        callback, current_user, {UserRole.EXPERT, UserRole.SPECIALIST, UserRole.ADMIN}
    ):
        return
    data = await state.get_data()
    try:
        work_type = await _ensure_work_type(state, current_user, data)
        related_orders = tuple(
            OrderNumberInput(
                order["raw"],
                order["normalized"],
                order.get("supplier_name"),
                ReconciliationState(
                    order.get("reconciliation_state", ReconciliationState.NO_FILE.value)
                ),
                order.get("reference_upload_id"),
            )
            for order in data.get("related_orders", [])
        )
        common = {
            "inspection_date": datetime.fromisoformat(data["inspection_date"]).date(),
            "order_number_raw": data["order_number_raw"],
            "order_number_normalized": data["order_number_normalized"],
            "supplier_id": data.get("supplier_id"),
            "status_id": int(data["status_id"]),
            "project_id": int(data["project_id"]),
            "rate": Decimal(data["rate"]),
            "comment": data.get("comment"),
            "related_orders": related_orders,
            "primary_supplier_name": data.get("primary_supplier_name"),
            "primary_reconciliation_state": ReconciliationState(
                data.get(
                    "primary_reconciliation_state",
                    ReconciliationState.NO_FILE.value,
                )
            ),
            "primary_reference_upload_id": data.get("primary_reference_upload_id"),
            "scenario": InspectionScenario(data["scenario"]),
            "vehicle_number": data.get("vehicle_number"),
        }
        service = InspectionService(session)
        if data.get("editing_saved_id"):
            inspection = await service.update(
                InspectionUpdateInput(
                    inspection_id=int(data["editing_saved_id"]),
                    changed_by_id=current_user.id,
                    **common,
                )
            )
        else:
            inspection = await service.create(
                InspectionInput(
                    author_id=current_user.id,
                    work_type=work_type,
                    **common,
                )
            )
        await session.commit()
    except (DomainError, DuplicateOrderNotAllowed, KeyError, ValueError) as exc:
        await session.rollback()
        await callback.answer("Не удалось сохранить", show_alert=True)
        await callback.message.answer(
            "⚠️ <b>Инспекция не сохранена</b>\n\n"
            f"{html.quote(str(exc))}\n\nПроверьте данные или начните заново: /add"
        )
        return

    was_edit = bool(data.get("editing_saved_id"))
    await state.clear()
    await callback.answer("Сохранено")
    await callback.message.answer(
        ("✅ <b>Инспекция обновлена</b>\n\n" if was_edit else "✅ <b>Инспекция сохранена</b>\n\n")
        + f"📦 Заказ: <code>{html.quote(inspection.order_number_raw)}</code>\n"
        + f"🧾 Номер записи: <b>#{inspection.id}</b>\n\n"
        + (
            "Изменения записаны в историю."
            if was_edit
            else "Можно быстро добавить следующий заказ:"
        ),
        reply_markup=(
            main_menu(current_user)
            if was_edit
            else after_save_keyboard(inspection.inspection_date, None, inspection.work_type)
        ),
    )
