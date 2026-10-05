from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import (
    ExportMode,
    InspectionScenario,
    InspectionWorkType,
    ProjectCode,
    ReconciliationState,
    SupplierStatus,
    UserRole,
    UserStatus,
)
from app.errors import DomainError, DuplicateOrderNotAllowed, EmptyExport, EntityNotFound
from app.models import (
    Inspection,
    InspectionRevision,
    InspectionStatus,
    Project,
    ReferenceOrder,
    ReferenceUpload,
    Supplier,
    User,
)
from app.routers.inspections import (
    _allowed_scenarios,
    inspection_order,
    inspection_related_order,
    start_saved_inspection_edit,
)
from app.seed import seed_reference_data
from app.services.exports import ExportService
from app.services.inspections import (
    InspectionInput,
    InspectionService,
    InspectionUpdateInput,
    OrderNumberInput,
)
from app.states import InspectionStates


def make_state() -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=901, user_id=901),
    )


def make_message(text: str) -> Message:
    message = MagicMock(spec=Message)
    message.text = text
    message.chat = SimpleNamespace(id=901)
    message.answer = AsyncMock()
    return message


async def create_user(session: AsyncSession, telegram_id: int, role: UserRole) -> User:
    user = User(
        telegram_id=telegram_id,
        full_name=f"Сотрудник {telegram_id}",
        requested_role=role,
        role=role,
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    await session.flush()
    return user


async def get_status(session: AsyncSession, code: InspectionScenario) -> InspectionStatus:
    status = await session.scalar(select(InspectionStatus).where(InspectionStatus.code == code))
    assert status is not None
    return status


async def get_project(session: AsyncSession, code: ProjectCode) -> Project:
    project = await session.scalar(select(Project).where(Project.code == code))
    assert project is not None
    return project


async def activate_reference_orders(
    session: AsyncSession, uploaded_by: User, *orders: str
) -> None:
    upload = ReferenceUpload(
        uploaded_by_id=uploaded_by.id,
        original_filename="test-reference.xlsx",
        sha256=("e" * 63) + str(uploaded_by.id % 10),
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


@pytest.mark.parametrize(
    ("work_type", "project_code", "expected"),
    [
        (
            InspectionWorkType.SPECIALIST,
            ProjectCode.THUNDER_AGRO_MAF,
            {InspectionScenario.NEXT, InspectionScenario.SAME_VEHICLE},
        ),
        (
            InspectionWorkType.EXPERT,
            ProjectCode.THUNDER_AGRO_MAF,
            {
                InspectionScenario.INSPECTION_STOP,
                InspectionScenario.SAME_VEHICLE,
                InspectionScenario.IDLE_TRIP,
                InspectionScenario.NEXT,
            },
        ),
        (
            InspectionWorkType.EXPERT,
            ProjectCode.THUNDER_SELF_PICKUP,
            {
                InspectionScenario.INSPECTION_STOP,
                InspectionScenario.SAME_VEHICLE,
                InspectionScenario.IDLE_TRIP,
                InspectionScenario.NEXT,
            },
        ),
        (
            InspectionWorkType.EXPERT,
            ProjectCode.X5_TECHMP_RVI,
            {
                InspectionScenario.INSPECTION_STOP,
                InspectionScenario.SAME_VEHICLE,
                InspectionScenario.IDLE_TRIP,
                InspectionScenario.NEXT,
            },
        ),
        (
            InspectionWorkType.EXPERT,
            ProjectCode.X5_SELF_PICKUP,
            {
                InspectionScenario.CONTROL_SHIPMENT,
                InspectionScenario.SHIFT,
                InspectionScenario.SAME_VEHICLE,
                InspectionScenario.IDLE_TRIP,
            },
        ),
        (
            InspectionWorkType.EXPERT,
            ProjectCode.THUNDER_WATERMELONS,
            {InspectionScenario.SHIFT, InspectionScenario.NEXT},
        ),
    ],
)
def test_project_scenario_matrix(
    work_type: InspectionWorkType,
    project_code: ProjectCode,
    expected: set[InspectionScenario],
) -> None:
    assert (
        _allowed_scenarios(
            {
                "work_type": work_type.value,
                "project_code": project_code.value,
                "is_duplicate": False,
            }
        )
        == expected
    )


def test_legacy_project_without_code_does_not_break_scenario_resolution() -> None:
    assert _allowed_scenarios(
        {
            "work_type": InspectionWorkType.EXPERT.value,
            "project_code": None,
            "is_duplicate": False,
        }
    ) == {
        InspectionScenario.INSPECTION_STOP,
        InspectionScenario.SAME_VEHICLE,
        InspectionScenario.IDLE_TRIP,
        InspectionScenario.NEXT,
    }


@pytest.mark.asyncio
async def test_missing_primary_order_is_blocked_by_reference_file(
    session: AsyncSession,
) -> None:
    await seed_reference_data(session)
    specialist = await create_user(session, 8290, UserRole.SPECIALIST)
    await activate_reference_orders(session, specialist, "KNOWN8290")
    state = make_state()
    await state.set_state(InspectionStates.order)
    await state.update_data(
        work_type=InspectionWorkType.SPECIALIST.value,
        inspection_date="2026-08-08",
        inspection_date_display="08.08.2026",
        related_orders=[],
    )
    message = make_message("missing8290")

    await inspection_order(message, state, session, current_user=specialist)

    data = await state.get_data()
    assert await state.get_state() == InspectionStates.order.state
    assert "order_number_normalized" not in data
    assert (
        "Номер заказа не найден в заявке. Пожалуйста, проверьте правильность ввода "
        "или свяжитесь с администратором."
    ) in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_found_orders_are_confirmed_and_missing_related_order_is_not_added(
    session: AsyncSession,
) -> None:
    await seed_reference_data(session)
    expert = await create_user(session, 8291, UserRole.EXPERT)
    await activate_reference_orders(session, expert, "MAIN8291", "EXTRA8291")
    state = make_state()
    await state.set_state(InspectionStates.order)
    await state.update_data(
        work_type=InspectionWorkType.EXPERT.value,
        inspection_date="2026-08-08",
        inspection_date_display="08.08.2026",
        project_id=1,
        project_name="Тест",
        project_code=ProjectCode.THUNDER_AGRO_MAF.value,
        related_orders=[],
    )
    primary_message = make_message("main8291")

    await inspection_order(primary_message, state, session, current_user=expert)

    assert "✅ Заказ найден." in primary_message.answer.await_args_list[0].args[0]
    await state.set_state(InspectionStates.related_order)
    await state.update_data(
        scenario=InspectionScenario.SAME_VEHICLE.value,
        status_allows_duplicate=False,
        related_orders=[],
    )
    found_message = make_message("extra8291")
    await inspection_related_order(found_message, state, session, current_user=expert)
    assert "✅ Заказ найден." in found_message.answer.await_args_list[0].args[0]
    assert (await state.get_data())["related_orders"][0]["normalized"] == "EXTRA8291"

    missing_message = make_message("missing8291")
    await inspection_related_order(missing_message, state, session, current_user=expert)
    assert len((await state.get_data())["related_orders"]) == 1
    assert "Номер заказа не найден в заявке" in missing_message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_x5_same_vehicle_requires_vehicle_and_exports_it(
    session: AsyncSession,
) -> None:
    await seed_reference_data(session)
    expert = await create_user(session, 8200, UserRole.EXPERT)
    admin = await create_user(session, 8201, UserRole.ADMIN)
    status = await get_status(session, InspectionScenario.SAME_VEHICLE)
    project = await get_project(session, ProjectCode.X5_SELF_PICKUP)
    data = dict(
        author_id=expert.id,
        work_type=InspectionWorkType.EXPERT,
        inspection_date=date(2026, 7, 10),
        order_number_raw="X5CAR1",
        order_number_normalized="X5CAR1",
        supplier_id=None,
        status_id=status.id,
        project_id=project.id,
        rate=Decimal("3000.00"),
        comment="одна машина",
        related_orders=(OrderNumberInput("X5CAR2", "X5CAR2"),),
    )
    with pytest.raises(DomainError, match="номер автомобиля"):
        await InspectionService(session).create(InspectionInput(**data))

    inspection = await InspectionService(session).create(
        InspectionInput(**data, vehicle_number="  А123ВС  ")
    )
    await session.commit()
    batch = await ExportService(session).reserve(
        requested_by_id=admin.id,
        period_from=date(2026, 7, 1),
        period_to=date(2026, 7, 31),
        expert_id=None,
        mode=ExportMode.FULL,
        affects_processing=True,
    )
    _, rows = await ExportService(session).load_rows(batch.id)

    assert inspection.vehicle_number == "А123ВС"
    assert (
        await InspectionService(session).count_for_user(
            expert.id, date(2026, 7, 1), date(2026, 7, 31)
        )
        == 1
    )
    assert rows[0].status_name == "отгружались в одной авто А123ВС"
    assert rows[1].status_name == "-"
    assert rows[1].rate == Decimal("0.00")


@pytest.mark.asyncio
async def test_expert_next_exports_as_control_shipment(session: AsyncSession) -> None:
    await seed_reference_data(session)
    expert = await create_user(session, 8292, UserRole.EXPERT)
    admin = await create_user(session, 8293, UserRole.ADMIN)
    status = await get_status(session, InspectionScenario.NEXT)
    project = await get_project(session, ProjectCode.THUNDER_AGRO_MAF)
    await InspectionService(session).create(
        InspectionInput(
            author_id=expert.id,
            work_type=InspectionWorkType.EXPERT,
            inspection_date=date(2026, 8, 8),
            order_number_raw="NEXT8292",
            order_number_normalized="NEXT8292",
            supplier_id=None,
            status_id=status.id,
            project_id=project.id,
            rate=Decimal("1500.00"),
            comment=None,
            scenario=InspectionScenario.NEXT,
        )
    )
    await session.commit()
    batch = await ExportService(session).reserve(
        requested_by_id=admin.id,
        period_from=date(2026, 8, 1),
        period_to=date(2026, 8, 31),
        expert_id=None,
        mode=ExportMode.FULL,
        affects_processing=True,
    )

    _, rows = await ExportService(session).load_rows(batch.id)

    assert rows[0].status_name == "контроль отгрузки"


@pytest.mark.asyncio
async def test_vehicle_number_is_not_carried_to_non_x5_project(
    session: AsyncSession,
) -> None:
    await seed_reference_data(session)
    expert = await create_user(session, 8210, UserRole.EXPERT)
    status = await get_status(session, InspectionScenario.SAME_VEHICLE)
    project = await get_project(session, ProjectCode.THUNDER_AGRO_MAF)

    inspection = await InspectionService(session).create(
        InspectionInput(
            author_id=expert.id,
            work_type=InspectionWorkType.EXPERT,
            inspection_date=date(2026, 7, 10),
            order_number_raw="NOTX5CAR1",
            order_number_normalized="NOTX5CAR1",
            supplier_id=None,
            status_id=status.id,
            project_id=project.id,
            rate=Decimal("3000.00"),
            comment=None,
            related_orders=(OrderNumberInput("NOTX5CAR2", "NOTX5CAR2"),),
            vehicle_number="АС123",
        )
    )

    assert inspection.vehicle_number is None


@pytest.mark.asyncio
async def test_specialist_duplicate_reuses_earliest_project(session: AsyncSession) -> None:
    await seed_reference_data(session)
    specialist = await create_user(session, 8202, UserRole.SPECIALIST)
    status = await get_status(session, InspectionScenario.NEXT)
    first_project = await get_project(session, ProjectCode.THUNDER_SELF_PICKUP)
    await InspectionService(session).create(
        InspectionInput(
            author_id=specialist.id,
            work_type=InspectionWorkType.SPECIALIST,
            inspection_date=date(2026, 6, 1),
            order_number_raw="DUPSPECIAL1",
            order_number_normalized="DUPSPECIAL1",
            supplier_id=None,
            status_id=status.id,
            project_id=first_project.id,
            rate=Decimal("1000.00"),
            comment=None,
        )
    )
    await session.commit()
    await activate_reference_orders(session, specialist, "DUPSPECIAL1")
    state = make_state()
    await state.set_state(InspectionStates.order)
    await state.update_data(
        work_type=InspectionWorkType.SPECIALIST.value,
        inspection_date="2026-07-10",
        inspection_date_display="10.07.2026",
        related_orders=[],
    )

    await inspection_order(make_message("dupspecial1"), state, session, current_user=specialist)
    saved = await state.get_data()

    assert saved["is_duplicate"] is True
    assert saved["project_id"] == first_project.id
    assert saved["project_code"] == ProjectCode.THUNDER_SELF_PICKUP.value
    assert await state.get_state() == InspectionStates.status.state


@pytest.mark.asyncio
async def test_expert_duplicate_keeps_project_selected_at_start(
    session: AsyncSession,
) -> None:
    await seed_reference_data(session)
    expert = await create_user(session, 8206, UserRole.EXPERT)
    status = await get_status(session, InspectionScenario.INSPECTION_STOP)
    old_project = await get_project(session, ProjectCode.THUNDER_AGRO_MAF)
    selected_project = await get_project(session, ProjectCode.THUNDER_SELF_PICKUP)
    await InspectionService(session).create(
        InspectionInput(
            author_id=expert.id,
            work_type=InspectionWorkType.EXPERT,
            inspection_date=date(2026, 6, 1),
            order_number_raw="DUPEXPERT1",
            order_number_normalized="DUPEXPERT1",
            supplier_id=None,
            status_id=status.id,
            project_id=old_project.id,
            rate=Decimal("1000.00"),
            comment=None,
        )
    )
    await session.commit()
    await activate_reference_orders(session, expert, "DUPEXPERT1")
    state = make_state()
    await state.set_state(InspectionStates.order)
    await state.update_data(
        work_type=InspectionWorkType.EXPERT.value,
        inspection_date="2026-07-10",
        inspection_date_display="10.07.2026",
        project_id=selected_project.id,
        project_name=selected_project.name,
        project_code=selected_project.code.value,
        related_orders=[],
    )

    message = make_message("dupexpert1")
    await inspection_order(message, state, session, current_user=expert)
    saved = await state.get_data()

    assert saved["is_duplicate"] is True
    assert saved["project_id"] == selected_project.id
    assert saved["project_code"] == ProjectCode.THUNDER_SELF_PICKUP.value
    assert await state.get_state() == InspectionStates.status.state
    assert message.answer.await_args_list[1].args[0] == "⚠️ <b>Найден такой же номер заказа</b>"
    status_prompt = message.answer.await_args_list[-1].args[0]
    assert "Доступны только статусы, разрешающие повтор." in status_prompt
    assert "Этот номер уже есть в данном виде работы" not in status_prompt
    assert "Выберите результат инспекции" not in status_prompt
    keyboard = message.answer.await_args_list[-1].kwargs["reply_markup"]
    labels = {button.text for row in keyboard.inline_keyboard for button in row}
    assert any("комиссионная инспекция" in label for label in labels)
    assert any("повторная инспекция" in label for label in labels)
    assert not any("осмотр / стоп-отгрузка" in label for label in labels)


@pytest.mark.asyncio
async def test_unchanged_order_keeps_saved_supplier_snapshot(
    session: AsyncSession,
) -> None:
    await seed_reference_data(session)
    specialist = await create_user(session, 8209, UserRole.SPECIALIST)
    state = make_state()
    await state.set_state(InspectionStates.order)
    await state.update_data(
        editing_saved_id=999,
        editing="order",
        work_type=InspectionWorkType.SPECIALIST.value,
        inspection_date="2026-07-10",
        inspection_date_display="10.07.2026",
        order_number_raw="SNAPSHOT1",
        order_number_normalized="SNAPSHOT1",
        primary_supplier_name="Сохранённый поставщик",
        primary_reconciliation_state=ReconciliationState.FOUND.value,
        primary_reference_upload_id=17,
        related_orders=[],
    )

    message = make_message("SNAPSHOT1")
    await inspection_order(message, state, session, current_user=specialist)
    saved = await state.get_data()

    assert saved["primary_supplier_name"] == "Сохранённый поставщик"
    assert saved["primary_reference_upload_id"] == 17
    assert saved["primary_reconciliation_state"] == ReconciliationState.FOUND.value
    messages = [call.args[0] for call in message.answer.await_args_list]
    assert any("✅ Заказ найден." in text for text in messages)
    assert not any("Проверьте корректность" in text for text in messages)


@pytest.mark.asyncio
async def test_admin_can_edit_any_employee_and_history_keeps_old_values(
    session: AsyncSession,
) -> None:
    await seed_reference_data(session)
    specialist = await create_user(session, 8203, UserRole.SPECIALIST)
    outsider = await create_user(session, 8204, UserRole.SPECIALIST)
    admin = await create_user(session, 8205, UserRole.ADMIN)
    status = await get_status(session, InspectionScenario.NEXT)
    project = await get_project(session, ProjectCode.THUNDER_AGRO_MAF)
    inspection = await InspectionService(session).create(
        InspectionInput(
            author_id=specialist.id,
            work_type=InspectionWorkType.SPECIALIST,
            inspection_date=date(2025, 7, 1),
            order_number_raw="HISTORY1",
            order_number_normalized="HISTORY1",
            supplier_id=None,
            status_id=status.id,
            project_id=project.id,
            rate=Decimal("700.00"),
            comment="старое",
            primary_supplier_name="Старый поставщик",
        )
    )
    await session.commit()

    with pytest.raises(EntityNotFound):
        await InspectionService(session).get_for_user(inspection.id, outsider.id)
    assert (
        await InspectionService(session).get_for_user(inspection.id, admin.id)
    ).id == inspection.id

    updated = await InspectionService(session).update(
        InspectionUpdateInput(
            inspection_id=inspection.id,
            changed_by_id=admin.id,
            inspection_date=date(2025, 7, 1),
            order_number_raw="HISTORY2",
            order_number_normalized="HISTORY2",
            supplier_id=None,
            status_id=status.id,
            project_id=project.id,
            rate=Decimal("1000.00"),
            comment="исправлено",
            primary_supplier_name="Новый поставщик",
        )
    )
    await session.commit()
    revision = await session.scalar(select(InspectionRevision))

    assert updated.author_id == specialist.id
    assert updated.rate == Decimal("1000.00")
    assert revision is not None and revision.changed_by_id == admin.id
    assert revision.before_data["orders"][0]["raw"] == "HISTORY1"
    assert revision.before_data["orders"][0]["supplier_name"] == "Старый поставщик"
    assert revision.before_data["rate"] == "700.00"
    assert revision.before_data["status_name"] == status.name
    assert revision.before_data["status_code"] == InspectionScenario.NEXT.value
    assert revision.before_data["project_name"] == project.name
    assert revision.before_data["project_code"] == project.code.value


@pytest.mark.asyncio
async def test_original_record_can_be_edited_after_repeat_was_created(
    session: AsyncSession,
) -> None:
    await seed_reference_data(session)
    specialist = await create_user(session, 8210, UserRole.SPECIALIST)
    regular = await get_status(session, InspectionScenario.NEXT)
    repeat = await get_status(session, InspectionScenario.REPEAT)
    project = await get_project(session, ProjectCode.THUNDER_AGRO_MAF)
    original = await InspectionService(session).create(
        InspectionInput(
            author_id=specialist.id,
            work_type=InspectionWorkType.SPECIALIST,
            inspection_date=date(2026, 7, 1),
            order_number_raw="DUPEDIT1",
            order_number_normalized="DUPEDIT1",
            supplier_id=None,
            status_id=regular.id,
            project_id=project.id,
            rate=Decimal("700.00"),
            comment=None,
        )
    )
    repeated = await InspectionService(session).create(
        InspectionInput(
            author_id=specialist.id,
            work_type=InspectionWorkType.SPECIALIST,
            inspection_date=date(2026, 7, 2),
            order_number_raw="DUPEDIT1",
            order_number_normalized="DUPEDIT1",
            supplier_id=None,
            status_id=repeat.id,
            project_id=project.id,
            rate=Decimal("800.00"),
            comment="повтор",
            scenario=InspectionScenario.REPEAT,
        )
    )
    await session.commit()

    updated = await InspectionService(session).update(
        InspectionUpdateInput(
            inspection_id=original.id,
            changed_by_id=specialist.id,
            inspection_date=original.inspection_date,
            order_number_raw="DUPEDIT1",
            order_number_normalized="DUPEDIT1",
            supplier_id=None,
            status_id=regular.id,
            project_id=project.id,
            rate=Decimal("1000.00"),
            comment="исправлено",
            scenario=InspectionScenario.NEXT,
        )
    )
    await session.commit()

    assert updated.rate == Decimal("1000.00")
    assert updated.comment == "исправлено"

    loaded = await InspectionService(session).get_for_user(original.id, specialist.id)
    state = make_state()
    await start_saved_inspection_edit(
        make_message(""),
        state,
        loaded,
        is_duplicate=loaded.status.allows_duplicate,
        page=3,
    )
    assert (await state.get_data())["editing_saved_page"] == 3
    await state.set_state(InspectionStates.order)
    await state.update_data(editing="order")
    await inspection_order(make_message("DUPEDIT1"), state, session, current_user=specialist)

    assert (await state.get_data())["is_duplicate"] is False

    with pytest.raises(DuplicateOrderNotAllowed):
        await InspectionService(session).update(
            InspectionUpdateInput(
                inspection_id=repeated.id,
                changed_by_id=specialist.id,
                inspection_date=repeated.inspection_date,
                order_number_raw="DUPEDIT1",
                order_number_normalized="DUPEDIT1",
                supplier_id=None,
                status_id=regular.id,
                project_id=project.id,
                rate=Decimal("800.00"),
                comment="нельзя превратить повтор в обычную запись",
                scenario=InspectionScenario.NEXT,
            )
        )


@pytest.mark.asyncio
async def test_historical_inactive_references_do_not_block_correction(
    session: AsyncSession,
) -> None:
    await seed_reference_data(session)
    specialist = await create_user(session, 8211, UserRole.SPECIALIST)
    status = await get_status(session, InspectionScenario.NEXT)
    project = await get_project(session, ProjectCode.THUNDER_AGRO_MAF)
    supplier = await session.scalar(
        select(Supplier).where(Supplier.status == SupplierStatus.ACTIVE)
    )
    assert supplier is not None
    inspection = await InspectionService(session).create(
        InspectionInput(
            author_id=specialist.id,
            work_type=InspectionWorkType.SPECIALIST,
            inspection_date=date(2025, 7, 1),
            order_number_raw="LEGACYREF1",
            order_number_normalized="LEGACYREF1",
            supplier_id=supplier.id,
            status_id=status.id,
            project_id=project.id,
            rate=Decimal("700.00"),
            comment=None,
        )
    )
    supplier.status = SupplierStatus.INACTIVE
    project.active = False
    await session.commit()

    loaded = await InspectionService(session).get_for_user(inspection.id, specialist.id)
    state = make_state()
    await start_saved_inspection_edit(make_message(""), state, loaded, is_duplicate=False)
    saved = await state.get_data()
    assert saved["supplier_id"] is None

    updated = await InspectionService(session).update(
        InspectionUpdateInput(
            inspection_id=inspection.id,
            changed_by_id=specialist.id,
            inspection_date=inspection.inspection_date,
            order_number_raw="LEGACYREF2",
            order_number_normalized="LEGACYREF2",
            supplier_id=None,
            status_id=status.id,
            project_id=project.id,
            rate=Decimal("900.00"),
            comment="исправлено",
            primary_reconciliation_state=ReconciliationState.FOUND,
            scenario=InspectionScenario.NEXT,
        )
    )
    await session.commit()

    assert updated.supplier_id is None
    assert updated.order_numbers[0].supplier_name_snapshot is None
    assert updated.order_numbers[0].reconciliation_state is ReconciliationState.FOUND


@pytest.mark.asyncio
async def test_legacy_status_remains_editable_and_visible_in_export(
    session: AsyncSession,
) -> None:
    await seed_reference_data(session)
    expert = await create_user(session, 8207, UserRole.EXPERT)
    admin = await create_user(session, 8208, UserRole.ADMIN)
    project = await get_project(session, ProjectCode.THUNDER_AGRO_MAF)
    legacy_status = InspectionStatus(
        name="осмотр legacy",
        normalized_name="осмотр legacy",
        code=None,
        allows_duplicate=False,
        supports_related_orders=False,
        active=True,
    )
    session.add(legacy_status)
    await session.flush()
    inspection = await InspectionService(session).create(
        InspectionInput(
            author_id=expert.id,
            work_type=InspectionWorkType.EXPERT,
            inspection_date=date(2026, 7, 11),
            order_number_raw="LEGACYSTATUS1",
            order_number_normalized="LEGACYSTATUS1",
            supplier_id=None,
            status_id=legacy_status.id,
            project_id=project.id,
            rate=Decimal("500.00"),
            comment=None,
        )
    )
    legacy_status.active = False
    await session.commit()

    await InspectionService(session).update(
        InspectionUpdateInput(
            inspection_id=inspection.id,
            changed_by_id=expert.id,
            inspection_date=inspection.inspection_date,
            order_number_raw=inspection.order_number_raw,
            order_number_normalized=inspection.order_number_normalized,
            supplier_id=None,
            status_id=legacy_status.id,
            project_id=project.id,
            rate=Decimal("600.00"),
            comment="история сохранена",
            scenario=InspectionScenario.NEXT,
        )
    )
    await session.commit()
    batch = await ExportService(session).reserve(
        requested_by_id=admin.id,
        period_from=date(2026, 7, 1),
        period_to=date(2026, 7, 31),
        expert_id=None,
        mode=ExportMode.FULL,
        affects_processing=True,
    )
    _, rows = await ExportService(session).load_rows(batch.id)

    assert rows[0].status_name == "осмотр legacy"


@pytest.mark.asyncio
async def test_employee_and_admin_can_soft_delete_and_deleted_records_are_excluded(
    session: AsyncSession,
) -> None:
    await seed_reference_data(session)
    specialist = await create_user(session, 8294, UserRole.SPECIALIST)
    admin = await create_user(session, 8295, UserRole.ADMIN)
    status = await get_status(session, InspectionScenario.NEXT)
    project = await get_project(session, ProjectCode.THUNDER_AGRO_MAF)
    service = InspectionService(session)

    own = await service.create(
        InspectionInput(
            author_id=specialist.id,
            work_type=InspectionWorkType.SPECIALIST,
            inspection_date=date(2026, 8, 8),
            order_number_raw="DELETEOWN8294",
            order_number_normalized="DELETEOWN8294",
            supplier_id=None,
            status_id=status.id,
            project_id=project.id,
            rate=Decimal("1000.00"),
            comment=None,
        )
    )
    by_admin = await service.create(
        InspectionInput(
            author_id=specialist.id,
            work_type=InspectionWorkType.SPECIALIST,
            inspection_date=date(2026, 8, 8),
            order_number_raw="DELETEADMIN8294",
            order_number_normalized="DELETEADMIN8294",
            supplier_id=None,
            status_id=status.id,
            project_id=project.id,
            rate=Decimal("1200.00"),
            comment=None,
        )
    )
    await session.commit()

    await service.delete(own.id, specialist.id)
    await service.delete(by_admin.id, admin.id)
    await session.commit()

    assert own.deleted_at is not None and own.deleted_by_id == specialist.id
    assert by_admin.deleted_at is not None and by_admin.deleted_by_id == admin.id
    assert await service.count_for_user(
        specialist.id, date(2026, 8, 1), date(2026, 8, 31)
    ) == 0
    assert await service.list_for_user(
        specialist.id,
        date.min,
        date.max,
        limit=10,
        offset=0,
    ) == []
    assert not await service.order_exists(
        "DELETEOWN8294", InspectionWorkType.SPECIALIST
    )
    with pytest.raises(EntityNotFound):
        await service.get_for_user(own.id, specialist.id)
    with pytest.raises(EmptyExport):
        await ExportService(session).reserve(
            requested_by_id=admin.id,
            period_from=date(2026, 8, 1),
            period_to=date(2026, 8, 31),
            expert_id=None,
            mode=ExportMode.FULL,
            affects_processing=True,
        )
    revisions = list(
        (
            await session.scalars(
                select(InspectionRevision).order_by(InspectionRevision.id)
            )
        ).all()
    )
    assert [item.before_data["change_type"] for item in revisions] == ["delete", "delete"]
    assert await session.scalar(select(func.count(Inspection.id))) == 2
