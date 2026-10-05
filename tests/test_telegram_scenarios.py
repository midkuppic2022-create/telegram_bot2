from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.enums import (
    ExportMode,
    InspectionScenario,
    InspectionWorkType,
    ProjectCode,
    UserRole,
    UserStatus,
)
from app.keyboards import (
    BatchCallback,
    ExportModeCallback,
    InspectionChoiceCallback,
    MyMonthCallback,
    PeriodCallback,
    RegistrationDecisionCallback,
    RegistrationRoleCallback,
)
from app.models import (
    Inspection,
    InspectionOrderNumber,
    ReferenceOrder,
    ReferenceUpload,
    RegistrationAdminNotification,
    User,
)
from app.routers.admin import registration_decision
from app.routers.common import cancel_command
from app.routers.inspections import (
    add_inspection,
    batch_same,
    inspection_comment_skip,
    inspection_date_text,
    inspection_order,
    inspection_project_choice,
    inspection_rate,
    inspection_related_done,
    inspection_related_order,
    inspection_save,
    inspection_status_choice,
    inspection_supplier_choice,
)
from app.routers.registration import (
    registration_invite_code,
    registration_name,
    registration_role,
    start,
)
from app.routers.reports import (
    _execute_export,
    _inspection_unit_count,
    _show_my_inspections,
    export_mode_selected,
    export_start,
    my_statistics,
    period_selected,
    report_start,
)
from app.routers.utils import require_active
from app.seed import seed_reference_data
from app.services.directories import DirectoryService
from app.services.inspections import InspectionInput, InspectionService
from app.services.users import UserService
from app.states import InspectionStates, RegistrationStates


def make_state(storage: MemoryStorage | None = None) -> FSMContext:
    return FSMContext(
        storage=storage or MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=100, user_id=100),
    )


def make_message(text: str = "") -> Message:
    message = MagicMock(spec=Message)
    message.text = text
    message.chat = SimpleNamespace(id=100)
    message.message_id = 55
    message.answer = AsyncMock()
    message.edit_reply_markup = AsyncMock()
    return message


def make_callback() -> CallbackQuery:
    callback = MagicMock(spec=CallbackQuery)
    callback.message = make_message()
    callback.answer = AsyncMock()
    return callback


async def activate_reference_orders(
    session: AsyncSession, uploaded_by: User, *orders: str
) -> None:
    upload = ReferenceUpload(
        uploaded_by_id=uploaded_by.id,
        original_filename="test-reference.xlsx",
        sha256=("f" * 63) + str(uploaded_by.id % 10),
        row_count=len(orders),
        warning_count=0,
        active=True,
    )
    upload.rows = [
        ReferenceOrder(
            order_number_raw=order,
            order_number_normalized=order.upper(),
            supplier_name="Тестовый поставщик",
        )
        for order in orders
    ]
    session.add(upload)
    await session.flush()


def test_export_message_counts_inspections_instead_of_order_rows() -> None:
    rows = [
        SimpleNamespace(inspection_id=1),
        SimpleNamespace(inspection_id=1),
        SimpleNamespace(inspection_id=2),
    ]

    assert _inspection_unit_count(rows) == 2


@pytest.mark.asyncio
async def test_registration_submission_and_approval(session: AsyncSession) -> None:
    await UserService(session).bootstrap_admins([777])
    await session.commit()
    state = make_state()
    message = make_message()
    bot = SimpleNamespace(
        set_my_commands=AsyncMock(),
        send_message=AsyncMock(
            side_effect=[
                SimpleNamespace(message_id=701),
                SimpleNamespace(message_id=702),
            ]
        ),
    )
    await start(
        message,
        state,
        None,
        session,
        bot,
        Settings(bot_token="123:TEST", admin_telegram_ids=[999]),
    )
    assert await state.get_state() == RegistrationStates.full_name.state

    message.text = "Иванов Иван"
    await registration_name(message, state)
    assert await state.get_state() == RegistrationStates.role.state

    callback = MagicMock(spec=CallbackQuery)
    callback.from_user = SimpleNamespace(id=100, username="ivanov")
    callback.message = make_message()
    callback.answer = AsyncMock()
    settings = Settings(bot_token="123:TEST", admin_telegram_ids=[999])
    await registration_role(
        callback,
        RegistrationRoleCallback(role=UserRole.EXPERT.value),
        state,
        session,
        bot,
        settings,
    )

    pending = await UserService(session).get_by_telegram_id(100)
    assert pending is not None
    assert pending.status is UserStatus.PENDING
    assert pending.role is None
    assert await state.get_state() is None
    notified_admins = {call.args[0] for call in bot.send_message.await_args_list}
    assert notified_admins == {777, 999}
    notifications = list(
        (
            await session.scalars(
                select(RegistrationAdminNotification).order_by(
                    RegistrationAdminNotification.admin_chat_id
                )
            )
        ).all()
    )
    assert [
        (item.admin_chat_id, item.telegram_message_id) for item in notifications
    ] == [(777, 701), (999, 702)]

    approved = await UserService(session).approve(pending.id, UserRole.EXPERT)
    await session.commit()
    assert approved.status is UserStatus.ACTIVE
    assert approved.role is UserRole.EXPERT


@pytest.mark.asyncio
async def test_registration_decision_updates_all_admin_notifications(
    session: AsyncSession,
) -> None:
    admin = User(
        telegram_id=777,
        full_name="Ксения",
        requested_role=UserRole.ADMIN,
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
    )
    session.add(admin)
    await session.flush()
    pending = await UserService(session).register_pending(
        telegram_id=12345,
        username="employee",
        full_name="Новый сотрудник",
        requested_role=UserRole.EXPERT,
    )
    await UserService(session).record_registration_notification(
        user_id=pending.id,
        admin_chat_id=777,
        telegram_message_id=701,
    )
    await UserService(session).record_registration_notification(
        user_id=pending.id,
        admin_chat_id=999,
        telegram_message_id=702,
    )
    await session.commit()

    callback = make_callback()
    callback.message.chat = SimpleNamespace(id=777)
    callback.message.message_id = 701
    bot = SimpleNamespace(
        edit_message_text=AsyncMock(),
        set_my_commands=AsyncMock(),
        send_message=AsyncMock(),
    )

    await registration_decision(
        callback,
        RegistrationDecisionCallback(user_id=pending.id, action=UserRole.EXPERT.value),
        session,
        admin,
        bot,
    )

    await session.refresh(pending)
    assert pending.status is UserStatus.ACTIVE
    assert pending.role is UserRole.EXPERT
    targets = {
        (call.kwargs["chat_id"], call.kwargs["message_id"])
        for call in bot.edit_message_text.await_args_list
    }
    assert targets == {(777, 701), (999, 702)}
    assert all(
        "Итоговая роль: 🦺 Эксперт" in call.kwargs["text"]
        and call.kwargs["reply_markup"] is None
        for call in bot.edit_message_text.await_args_list
    )


@pytest.mark.asyncio
async def test_stale_registration_notification_is_replaced_with_result(
    session: AsyncSession,
) -> None:
    admin = User(
        telegram_id=777,
        full_name="Ксения",
        requested_role=UserRole.ADMIN,
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
    )
    employee = User(
        telegram_id=12345,
        full_name="Сотрудник",
        requested_role=UserRole.SPECIALIST,
        role=UserRole.SPECIALIST,
        status=UserStatus.ACTIVE,
    )
    session.add_all([admin, employee])
    await session.commit()

    callback = make_callback()
    callback.message.chat = SimpleNamespace(id=888)
    callback.message.message_id = 703
    bot = SimpleNamespace(
        edit_message_text=AsyncMock(),
        set_my_commands=AsyncMock(),
        send_message=AsyncMock(),
    )

    await registration_decision(
        callback,
        RegistrationDecisionCallback(user_id=employee.id, action=UserRole.EXPERT.value),
        session,
        admin,
        bot,
    )

    bot.edit_message_text.assert_awaited_once()
    edit = bot.edit_message_text.await_args
    assert (edit.kwargs["chat_id"], edit.kwargs["message_id"]) == (888, 703)
    assert "Итоговая роль: 📋 Специалист" in edit.kwargs["text"]
    callback.answer.assert_awaited_once_with(
        "Заявка уже обработана. Уведомление обновлено."
    )
    await session.refresh(employee)
    assert employee.role is UserRole.SPECIALIST


@pytest.mark.asyncio
async def test_registration_invite_code_is_required(session: AsyncSession) -> None:
    state = make_state()
    message = make_message()
    bot = SimpleNamespace(set_my_commands=AsyncMock(), send_message=AsyncMock())
    settings = Settings(
        bot_token="123:TEST",
        admin_telegram_ids=[999],
        registration_invite_code="Kompas-2026",
    )

    await start(message, state, None, session, bot, settings)
    assert await state.get_state() == RegistrationStates.invite_code.state

    wrong = make_message("wrong")
    await registration_invite_code(wrong, state, settings)
    assert await state.get_state() == RegistrationStates.invite_code.state

    accepted = make_message("kompas-2026")
    await registration_invite_code(accepted, state, settings)
    assert await state.get_state() == RegistrationStates.full_name.state


@pytest.mark.asyncio
async def test_cancel_clears_active_dialog() -> None:
    state = make_state()
    await state.set_state(InspectionStates.comment)
    await state.update_data(order_number="ABC1")
    message = make_message("/cancel")

    await cancel_command(message, state, None)

    assert await state.get_state() is None
    assert await state.get_data() == {}
    message.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_fsm_data_survives_context_recreation() -> None:
    storage = MemoryStorage()
    first_context = make_state(storage)
    await first_context.set_state(InspectionStates.comment)
    await first_context.update_data(order_number_normalized="ABC123")

    restored_context = make_state(storage)
    assert await restored_context.get_state() == InspectionStates.comment.state
    assert (await restored_context.get_data())["order_number_normalized"] == "ABC123"


@pytest.mark.asyncio
async def test_role_guard_allows_specialist_in_add_flow() -> None:
    user = SimpleNamespace(
        status=UserStatus.ACTIVE,
        role=UserRole.SPECIALIST,
        full_name="Специалист",
    )
    message = make_message()

    allowed = await require_active(
        message, user, {UserRole.EXPERT, UserRole.SPECIALIST, UserRole.ADMIN}
    )

    assert allowed is True
    message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_report_uses_current_month_by_default(session: AsyncSession) -> None:
    specialist = User(
        telegram_id=500,
        full_name="Специалист",
        requested_role=UserRole.SPECIALIST,
        role=UserRole.SPECIALIST,
        status=UserStatus.ACTIVE,
    )
    session.add(specialist)
    await session.flush()
    state = make_state()
    message = make_message("/report")

    settings = Settings(bot_token="123:TEST", admin_telegram_ids=[999])
    await report_start(
        message,
        state,
        session,
        specialist,
        settings,
    )

    data = await state.get_data()
    today = datetime.now(settings.timezone).date()
    month_start = today.replace(day=1)
    next_month = (
        date(today.year + 1, 1, 1) if today.month == 12 else date(today.year, today.month + 1, 1)
    )
    month_end = next_month - timedelta(days=1)
    assert data["purpose"] == "report"
    assert data["period_from"] == month_start.isoformat()
    assert data["period_to"] == month_end.isoformat()
    assert message.answer.await_count == 1
    text = message.answer.await_args.args[0]
    assert "Общий отчёт" in text
    assert f"{month_start:%d.%m.%Y} — {month_end:%d.%m.%Y}" in text


@pytest.mark.asyncio
async def test_specialist_can_start_personal_excel_export(session: AsyncSession) -> None:
    specialist = User(
        telegram_id=551,
        full_name="Специалист без Excel",
        requested_role=UserRole.SPECIALIST,
        role=UserRole.SPECIALIST,
        status=UserStatus.ACTIVE,
    )
    session.add(specialist)
    await session.flush()
    message = make_message("/export")

    await export_start(
        message,
        make_state(),
        session,
        specialist,
        Settings(bot_token="123:TEST", admin_telegram_ids=[999]),
    )

    response = message.answer.await_args.args[0]
    assert "только ваши инспекции" in response
    assert "отдельные вкладки «Специалисты» и «Эксперты»" in response
    assert "листы АК, СВ и Х5" not in response
    assert "Выберите период" in response


@pytest.mark.asyncio
async def test_employee_export_period_skips_employee_filter(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    expert = User(
        telegram_id=552,
        full_name="Эксперт с личной выгрузкой",
        requested_role=UserRole.EXPERT,
        role=UserRole.EXPERT,
        status=UserStatus.ACTIVE,
    )
    session.add(expert)
    await session.flush()
    state = make_state()
    callback = make_callback()
    execute_export = AsyncMock()
    monkeypatch.setattr("app.routers.reports._execute_export", execute_export)

    await period_selected(
        callback,
        PeriodCallback(choice="current", purpose="export"),
        state,
        session,
        expert,
        Settings(bot_token="123:TEST", admin_telegram_ids=[999]),
    )

    data = await state.get_data()
    assert data["expert_id"] == expert.id
    assert data["expert_name"] == expert.full_name
    assert execute_export.await_args.args[3] is expert
    assert execute_export.await_args.args[5] is ExportMode.FULL
    assert execute_export.await_args.kwargs["affects_processing"] is False
    callback.message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_specialist_inspection_flow_skips_fuel(session: AsyncSession) -> None:
    await seed_reference_data(session)
    specialist = User(
        telegram_id=550,
        full_name="Специалист без ГСМ",
        requested_role=UserRole.SPECIALIST,
        role=UserRole.SPECIALIST,
        status=UserStatus.ACTIVE,
    )
    session.add(specialist)
    await session.flush()
    await activate_reference_orders(session, specialist, "SPEC777")
    directories = DirectoryService(session)
    status = next(
        item for item in await directories.list_statuses() if item.code is InspectionScenario.NEXT
    )
    project = next(
        item
        for item in await directories.list_projects()
        if item.code is ProjectCode.THUNDER_AGRO_MAF
    )
    settings = Settings(bot_token="123:TEST", admin_telegram_ids=[999])
    state = make_state()

    start_message = make_message("/add")
    await add_inspection(start_message, state, specialist)
    assert "Шаг 1 из 7" in start_message.answer.await_args.args[0]
    assert "fuel" not in await state.get_data()

    date_message = make_message("14.07.2026")
    await inspection_date_text(date_message, state, settings, session)
    assert "Поставщика бот определит" not in date_message.answer.await_args.args[0]
    await inspection_order(make_message("spec777"), state, session)
    assert await state.get_state() == InspectionStates.status.state
    status_callback = make_callback()
    await inspection_status_choice(
        status_callback,
        InspectionChoiceCallback(kind="status", entity_id=status.id),
        state,
        session,
    )
    assert "Шаг 4 из 7" in status_callback.message.answer.await_args.args[0]
    await inspection_project_choice(
        make_callback(),
        InspectionChoiceCallback(kind="project", entity_id=project.id),
        state,
        session,
    )
    rate_message = make_message("1500")
    await inspection_rate(rate_message, state)
    assert await state.get_state() == InspectionStates.comment.state
    assert "Шаг 6 из 7" in rate_message.answer.await_args.args[0]

    confirm_callback = make_callback()
    await inspection_comment_skip(confirm_callback, state)
    confirmation_text = confirm_callback.message.answer.await_args.args[0]
    confirmation_keyboard = confirm_callback.message.answer.await_args.kwargs["reply_markup"]
    confirmation_buttons = {
        button.text for row in confirmation_keyboard.inline_keyboard for button in row
    }
    assert "ГСМ" not in confirmation_text
    assert "Шаг 7 из 7" in confirmation_text
    assert "✏️ ⛽ ГСМ" not in confirmation_buttons

    await inspection_save(
        make_callback(),
        state,
        session,
        specialist,
        SimpleNamespace(send_message=AsyncMock()),
        settings,
    )
    inspection = await session.scalar(
        select(Inspection).where(Inspection.order_number_normalized == "SPEC777")
    )
    assert inspection is not None
    assert inspection.fuel is None
    assert inspection.supplier_id is None


@pytest.mark.asyncio
async def test_empty_export_returns_to_menu_without_internal_error(
    session: AsyncSession,
) -> None:
    admin = User(
        telegram_id=501,
        full_name="Администратор",
        requested_role=UserRole.ADMIN,
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
    )
    session.add(admin)
    await session.commit()
    state = make_state()
    await state.update_data(
        purpose="export",
        period_from="2026-07-01",
        period_to="2026-07-31",
        expert_id=None,
    )
    callback = make_callback()

    await _execute_export(
        callback,
        state,
        session,
        admin,
        Settings(bot_token="123:TEST", admin_telegram_ids=[999]),
        ExportMode.NEW,
        affects_processing=True,
    )

    assert await state.get_data() == {}
    text = callback.message.answer.await_args.args[0]
    assert "Нет данных для выгрузки" in text
    assert callback.message.answer.await_args.kwargs["reply_markup"] is not None


@pytest.mark.asyncio
async def test_stale_export_mode_button_shows_alert(session: AsyncSession) -> None:
    admin = User(
        telegram_id=502,
        full_name="Администратор",
        requested_role=UserRole.ADMIN,
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
    )
    session.add(admin)
    await session.commit()
    state = make_state()
    callback = make_callback()

    await export_mode_selected(
        callback,
        ExportModeCallback(mode=ExportMode.NEW.value),
        state,
        session,
        admin,
        Settings(bot_token="123:TEST", admin_telegram_ids=[999]),
    )

    callback.answer.assert_awaited_once_with(
        "Эта кнопка уже неактуальна. Откройте выгрузку заново.", show_alert=True
    )
    callback.message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_expert_flow_and_reuse_date_batch(session: AsyncSession) -> None:
    await seed_reference_data(session)
    expert = User(
        telegram_id=600,
        full_name="Петросян Артур",
        requested_role=UserRole.EXPERT,
        role=UserRole.EXPERT,
        status=UserStatus.ACTIVE,
    )
    session.add(expert)
    await session.flush()
    await activate_reference_orders(session, expert, "YUG777")
    directories = DirectoryService(session)
    status = next(
        item
        for item in await directories.list_statuses()
        if item.code is InspectionScenario.INSPECTION_STOP
    )
    project = next(
        item
        for item in await directories.list_projects()
        if item.code is ProjectCode.THUNDER_AGRO_MAF
    )
    settings = Settings(bot_token="123:TEST", admin_telegram_ids=[999])
    state = make_state()

    await add_inspection(make_message("/add"), state, expert)
    assert await state.get_state() == InspectionStates.date.state
    await inspection_date_text(make_message("14.07.2026"), state, settings, session)
    assert await state.get_state() == InspectionStates.project.state

    callback = make_callback()
    await inspection_project_choice(
        callback,
        InspectionChoiceCallback(kind="project", entity_id=project.id),
        state,
        session,
    )
    await inspection_order(make_message("yug777"), state, session)
    assert await state.get_state() == InspectionStates.status.state
    callback = make_callback()
    await inspection_status_choice(
        callback,
        InspectionChoiceCallback(kind="status", entity_id=status.id),
        state,
        session,
    )
    await inspection_rate(make_message("1500,50"), state)
    await inspection_comment_skip(make_callback(), state)
    assert await state.get_state() == InspectionStates.confirm.state

    await inspection_save(
        make_callback(),
        state,
        session,
        expert,
        SimpleNamespace(send_message=AsyncMock()),
        settings,
    )
    assert await session.scalar(select(func.count(Inspection.id))) == 1
    assert await state.get_state() is None

    await batch_same(
        make_callback(),
        BatchCallback(
            action="same",
            inspection_date="2026-07-14",
        ),
        state,
        session,
        expert,
    )
    data = await state.get_data()
    assert await state.get_state() == InspectionStates.project.state
    assert data["inspection_date"] == "2026-07-14"
    assert "supplier_id" not in data


@pytest.mark.asyncio
async def test_stale_supplier_button_returns_clear_message(session: AsyncSession) -> None:
    await seed_reference_data(session)
    state = make_state()
    await state.set_state(InspectionStates.supplier_query)
    await state.update_data(
        work_type=InspectionWorkType.SPECIALIST.value,
        is_duplicate=False,
    )
    callback = make_callback()

    await inspection_supplier_choice(
        callback,
        InspectionChoiceCallback(kind="supplier", entity_id=999),
        state,
        session,
    )

    callback.answer.assert_awaited_once_with(
        "Поставщик теперь определяется автоматически", show_alert=True
    )
    assert await state.get_state() == InspectionStates.status.state


@pytest.mark.asyncio
async def test_my_inspections_include_previous_month(session: AsyncSession) -> None:
    await seed_reference_data(session)
    expert = User(
        telegram_id=603,
        full_name="Эксперт с историей",
        requested_role=UserRole.EXPERT,
        role=UserRole.EXPERT,
        status=UserStatus.ACTIVE,
    )
    session.add(expert)
    await session.flush()
    directories = DirectoryService(session)
    status = next(
        item
        for item in await directories.list_statuses()
        if item.code is InspectionScenario.INSPECTION_STOP
    )
    project = next(
        item
        for item in await directories.list_projects()
        if item.code is ProjectCode.THUNDER_AGRO_MAF
    )
    service = InspectionService(session)

    for inspection_date, order_number in (
        (date(2026, 7, 31), "JULY603"),
        (date(2026, 8, 1), "AUGUST603"),
    ):
        await service.create(
            InspectionInput(
                author_id=expert.id,
                work_type=InspectionWorkType.EXPERT,
                inspection_date=inspection_date,
                order_number_raw=order_number,
                order_number_normalized=order_number,
                supplier_id=None,
                status_id=status.id,
                project_id=project.id,
                rate=Decimal("1500.00"),
                comment=None,
            )
        )
    await session.commit()

    message = make_message()
    await _show_my_inspections(message, session, expert, page=0)

    response = message.answer.await_args.args[0]
    assert "JULY603" in response
    assert "AUGUST603" in response
    assert "Все сохранённые записи" in response


@pytest.mark.asyncio
async def test_my_statistics_keeps_history_button_when_current_month_is_empty(
    session: AsyncSession,
) -> None:
    await seed_reference_data(session)
    expert = User(
        telegram_id=604,
        full_name="Эксперт с архивом",
        requested_role=UserRole.EXPERT,
        role=UserRole.EXPERT,
        status=UserStatus.ACTIVE,
    )
    session.add(expert)
    await session.flush()
    directories = DirectoryService(session)
    status = next(
        item
        for item in await directories.list_statuses()
        if item.code is InspectionScenario.INSPECTION_STOP
    )
    project = next(
        item
        for item in await directories.list_projects()
        if item.code is ProjectCode.THUNDER_AGRO_MAF
    )
    await InspectionService(session).create(
        InspectionInput(
            author_id=expert.id,
            work_type=InspectionWorkType.EXPERT,
            inspection_date=date(2025, 1, 1),
            order_number_raw="ARCHIVE604",
            order_number_normalized="ARCHIVE604",
            supplier_id=None,
            status_id=status.id,
            project_id=project.id,
            rate=Decimal("1000.00"),
            comment=None,
        )
    )
    await session.commit()
    message = make_message("/my")

    await my_statistics(
        message,
        make_state(),
        session,
        expert,
        Settings(bot_token="123:TEST", admin_telegram_ids=[999]),
    )

    keyboard = message.answer.await_args.kwargs["reply_markup"]
    button = keyboard.inline_keyboard[0][0]
    assert button.text == "📦 Показать заказы"
    parsed = MyMonthCallback.unpack(button.callback_data)
    assert parsed.action == "list"


@pytest.mark.asyncio
async def test_old_dialog_without_work_type_resumes_after_deploy(
    session: AsyncSession,
) -> None:
    await seed_reference_data(session)
    expert = User(
        telegram_id=601,
        full_name="Эксперт со старым диалогом",
        requested_role=UserRole.EXPERT,
        role=UserRole.EXPERT,
        status=UserStatus.ACTIVE,
    )
    session.add(expert)
    await session.flush()
    await activate_reference_orders(session, expert, "LEGACY601")
    state = make_state()
    await state.set_state(InspectionStates.order)
    await state.update_data(related_orders=[])

    await inspection_order(make_message("legacy601"), state, session, current_user=expert)

    data = await state.get_data()
    assert data["work_type"] == "expert"
    assert data["order_number_normalized"] == "LEGACY601"
    assert await state.get_state() == InspectionStates.project.state


@pytest.mark.asyncio
async def test_related_orders_flow_saves_one_inspection(session: AsyncSession) -> None:
    await seed_reference_data(session)
    expert = User(
        telegram_id=700,
        full_name="Эксперт связанной отгрузки",
        requested_role=UserRole.EXPERT,
        role=UserRole.EXPERT,
        status=UserStatus.ACTIVE,
    )
    session.add(expert)
    await session.flush()
    await activate_reference_orders(
        session, expert, "MAIN700", "EXTRA701", "EXTRA702"
    )
    directories = DirectoryService(session)
    status = next(
        item
        for item in await directories.list_statuses()
        if item.code is InspectionScenario.SAME_VEHICLE
    )
    project = next(
        item
        for item in await directories.list_projects()
        if item.code is ProjectCode.THUNDER_AGRO_MAF
    )
    settings = Settings(bot_token="123:TEST", admin_telegram_ids=[999])
    state = make_state()

    await add_inspection(make_message("/add"), state, expert)
    await inspection_date_text(make_message("14.07.2026"), state, settings, session)
    await inspection_project_choice(
        make_callback(),
        InspectionChoiceCallback(kind="project", entity_id=project.id),
        state,
        session,
    )
    await inspection_order(make_message("main700"), state, session)
    related_callback = make_callback()
    await inspection_status_choice(
        related_callback,
        InspectionChoiceCallback(kind="status", entity_id=status.id),
        state,
        session,
    )
    assert await state.get_state() == InspectionStates.related_order.state
    assert "Каждый заказ попадёт" not in related_callback.message.answer.await_args.args[0]
    await inspection_related_order(make_message("extra701"), state, session)
    await inspection_related_order(make_message("extra702"), state, session)
    await inspection_related_done(make_callback(), state, session)
    assert await state.get_state() == InspectionStates.rate.state
    await inspection_rate(make_message("1500"), state)
    await inspection_comment_skip(make_callback(), state)
    await inspection_save(
        make_callback(),
        state,
        session,
        expert,
        SimpleNamespace(send_message=AsyncMock()),
        settings,
    )

    orders = list(
        (
            await session.scalars(
                select(InspectionOrderNumber).order_by(InspectionOrderNumber.position)
            )
        ).all()
    )
    assert [item.order_number_raw for item in orders] == [
        "main700",
        "extra701",
        "extra702",
    ]
