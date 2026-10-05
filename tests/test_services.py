from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import (
    ExportMode,
    InspectionScenario,
    InspectionWorkType,
    ProjectCode,
    SupplierStatus,
    UserRole,
    UserStatus,
)
from app.errors import DomainError, DuplicateOrderNotAllowed, EmptyExport
from app.models import (
    ExportBatch,
    Inspection,
    InspectionRevision,
    InspectionStatus,
    Project,
    Supplier,
    User,
)
from app.seed import seed_reference_data
from app.services.directories import DirectoryService
from app.services.exports import ExportService
from app.services.inspections import (
    InspectionInput,
    InspectionService,
    InspectionUpdateInput,
    OrderNumberInput,
)
from app.services.users import UserService


async def create_user(session: AsyncSession, telegram_id: int = 100) -> User:
    user = User(
        telegram_id=telegram_id,
        full_name="Петросян Артур",
        requested_role=UserRole.EXPERT,
        role=UserRole.EXPERT,
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    await session.flush()
    return user


async def reference_entities(
    session: AsyncSession,
) -> tuple[Supplier, InspectionStatus, InspectionStatus, Project]:
    supplier = await session.scalar(
        select(Supplier).where(Supplier.status == SupplierStatus.ACTIVE)
    )
    regular = await session.scalar(
        select(InspectionStatus).where(
            InspectionStatus.code == InspectionScenario.INSPECTION_STOP
        )
    )
    repeated = await session.scalar(
        select(InspectionStatus).where(InspectionStatus.code == InspectionScenario.REPEAT)
    )
    project = await session.scalar(
        select(Project).where(Project.code == ProjectCode.THUNDER_AGRO_MAF)
    )
    assert supplier and regular and repeated and project
    return supplier, regular, repeated, project


async def create_inspection(
    session: AsyncSession,
    user: User,
    *,
    order: str,
    status: InspectionStatus,
) -> Inspection:
    supplier, _, _, project = await reference_entities(session)
    if user.role is UserRole.SPECIALIST and status.code not in {
        InspectionScenario.COMMISSION,
        InspectionScenario.REPEAT,
    }:
        specialist_status = await session.scalar(
            select(InspectionStatus).where(
                InspectionStatus.code == InspectionScenario.NEXT
            )
        )
        assert specialist_status
        status = specialist_status
    return await InspectionService(session).create(
        InspectionInput(
            author_id=user.id,
            work_type=(
                InspectionWorkType.SPECIALIST
                if user.role is UserRole.SPECIALIST
                else InspectionWorkType.EXPERT
            ),
            inspection_date=date(2026, 7, 14),
            order_number_raw=order,
            order_number_normalized=order.upper(),
            supplier_id=supplier.id,
            status_id=status.id,
            project_id=project.id,
            rate=Decimal("1500.00"),
            comment=None,
        )
    )


@pytest.mark.asyncio
async def test_seed_is_idempotent_and_bootstraps_admin(session: AsyncSession) -> None:
    await seed_reference_data(session)
    await seed_reference_data(session)
    await UserService(session).bootstrap_admins([999])
    await UserService(session).bootstrap_admins([999])
    await session.commit()

    assert await session.scalar(select(func.count(InspectionStatus.id))) == 8
    assert await session.scalar(select(func.count(Project.id))) == 5
    assert await session.scalar(select(func.count(Supplier.id))) == 63
    admin = await UserService(session).get_by_telegram_id(999)
    assert admin and admin.role is UserRole.ADMIN and admin.status is UserStatus.ACTIVE


@pytest.mark.asyncio
async def test_active_employee_can_be_promoted_and_listed_as_admin(
    session: AsyncSession,
) -> None:
    employee = await create_user(session, telegram_id=998)

    promoted = await UserService(session).change_role(employee.id, UserRole.ADMIN)
    await session.commit()

    assert promoted.role is UserRole.ADMIN
    assert promoted.requested_role is UserRole.ADMIN
    assert [user.id for user in await UserService(session).list_active_admins()] == [
        employee.id
    ]


@pytest.mark.asyncio
async def test_duplicate_requires_allowed_status(session: AsyncSession) -> None:
    await seed_reference_data(session)
    user = await create_user(session)
    _, regular, repeated, _ = await reference_entities(session)
    await create_inspection(session, user, order="YUG123", status=regular)
    await session.commit()

    with pytest.raises(DuplicateOrderNotAllowed):
        await create_inspection(session, user, order="YUG123", status=regular)
    await session.rollback()

    user = await session.scalar(select(User).where(User.telegram_id == 100))
    repeated = await session.scalar(
        select(InspectionStatus).where(InspectionStatus.code == InspectionScenario.REPEAT)
    )
    assert user and repeated
    second = await create_inspection(session, user, order="YUG123", status=repeated)
    await session.commit()
    assert second.id is not None
    assert await session.scalar(select(func.count(Inspection.id))) == 2


@pytest.mark.asyncio
async def test_same_order_is_allowed_for_different_work_types(
    session: AsyncSession,
) -> None:
    await seed_reference_data(session)
    expert = await create_user(session, telegram_id=101)
    specialist = await create_user(session, telegram_id=102)
    specialist.role = UserRole.SPECIALIST
    _, regular, _, _ = await reference_entities(session)

    await create_inspection(session, expert, order="SHARED1", status=regular)
    specialist_record = await create_inspection(
        session, specialist, order="SHARED1", status=regular
    )
    await session.commit()

    assert specialist_record.work_type is InspectionWorkType.SPECIALIST
    assert await session.scalar(select(func.count(Inspection.id))) == 2

    with pytest.raises(DuplicateOrderNotAllowed):
        await create_inspection(session, expert, order="SHARED1", status=regular)


@pytest.mark.asyncio
async def test_related_orders_are_saved_in_one_inspection(session: AsyncSession) -> None:
    await seed_reference_data(session)
    expert = await create_user(session, telegram_id=103)
    supplier, _, _, project = await reference_entities(session)
    shipped = await session.scalar(
        select(InspectionStatus).where(
            InspectionStatus.code == InspectionScenario.SAME_VEHICLE
        )
    )
    assert shipped and shipped.supports_related_orders

    inspection = await InspectionService(session).create(
        InspectionInput(
            author_id=expert.id,
            work_type=InspectionWorkType.EXPERT,
            inspection_date=date(2026, 7, 14),
            order_number_raw="MAIN1",
            order_number_normalized="MAIN1",
            supplier_id=supplier.id,
            status_id=shipped.id,
            project_id=project.id,
            rate=Decimal("1500.00"),
            comment=None,
            related_orders=(
                OrderNumberInput("EXTRA2", "EXTRA2"),
                OrderNumberInput("EXTRA3", "EXTRA3"),
            ),
        )
    )
    await session.commit()

    assert [item.order_number_raw for item in inspection.order_numbers] == [
        "MAIN1",
        "EXTRA2",
        "EXTRA3",
    ]

    admin = await create_user(session, telegram_id=107)
    admin.role = UserRole.ADMIN
    batch = await ExportService(session).reserve(
        requested_by_id=admin.id,
        period_from=date(2026, 7, 1),
        period_to=date(2026, 7, 31),
        expert_id=None,
        mode=ExportMode.FULL,
        affects_processing=True,
    )
    _, rows = await ExportService(session).load_rows(batch.id)
    assert [row.order_number for row in rows] == ["MAIN1", "EXTRA2", "EXTRA3"]
    assert all(row.normalized_orders == ("MAIN1", "EXTRA2", "EXTRA3") for row in rows)
    assert [row.rate for row in rows] == [
        Decimal("1500.00"),
        Decimal("0.00"),
        Decimal("0.00"),
    ]
    assert [row.status_name for row in rows] == [
        "отгружались в одной авто",
        "-",
        "-",
    ]
    assert all(row.created_at == rows[0].created_at for row in rows)
    assert all(row.modified_at == rows[0].modified_at for row in rows)
    assert all(row.processed_at == rows[0].processed_at for row in rows)


@pytest.mark.asyncio
async def test_role_change_does_not_reclassify_existing_inspection(
    session: AsyncSession,
) -> None:
    await seed_reference_data(session)
    user = await create_user(session, telegram_id=104)
    _, regular, _, _ = await reference_entities(session)
    inspection = await create_inspection(session, user, order="ROLE1", status=regular)

    await UserService(session).change_role(user.id, UserRole.SPECIALIST)
    await session.commit()

    assert user.role is UserRole.SPECIALIST
    assert inspection.work_type is InspectionWorkType.EXPERT


@pytest.mark.asyncio
async def test_cross_role_match_is_not_highlighted_as_duplicate(
    session: AsyncSession,
) -> None:
    await seed_reference_data(session)
    expert = await create_user(session, telegram_id=108)
    specialist = await create_user(session, telegram_id=109)
    specialist.role = UserRole.SPECIALIST
    admin = await create_user(session, telegram_id=110)
    admin.role = UserRole.ADMIN
    _, regular, _, _ = await reference_entities(session)
    await create_inspection(session, expert, order="CROSSROLE1", status=regular)
    await create_inspection(session, specialist, order="CROSSROLE1", status=regular)
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

    assert len(rows) == 2
    assert all(not row.is_duplicate for row in rows)


@pytest.mark.asyncio
async def test_changed_exported_inspection_returns_to_new_export(
    session: AsyncSession,
) -> None:
    await seed_reference_data(session)
    expert = await create_user(session, telegram_id=105)
    admin = await create_user(session, telegram_id=106)
    admin.role = UserRole.ADMIN
    supplier, regular, _, project = await reference_entities(session)
    inspection = await create_inspection(session, expert, order="EDIT1", status=regular)
    await session.commit()

    first = await ExportService(session).reserve(
        requested_by_id=admin.id,
        period_from=date(2026, 7, 1),
        period_to=date(2026, 7, 31),
        expert_id=None,
        mode=ExportMode.NEW,
        affects_processing=True,
    )
    await ExportService(session).mark_sent(
        first.id, telegram_chat_id=1, telegram_message_id=1
    )
    await session.commit()
    assert await InspectionService(session).is_processed(inspection.id)

    updated = await InspectionService(session).update(
        InspectionUpdateInput(
            inspection_id=inspection.id,
            changed_by_id=expert.id,
            inspection_date=inspection.inspection_date,
            order_number_raw="EDIT1",
            order_number_normalized="EDIT1",
            supplier_id=supplier.id,
            status_id=regular.id,
            project_id=project.id,
            rate=Decimal("1700.00"),
            comment="Исправлено после выгрузки",
        )
    )
    await session.commit()

    assert updated.updated_at is not None
    assert await session.scalar(select(func.count(InspectionRevision.id))) == 1
    assert not await InspectionService(session).is_processed(inspection.id)

    second = await ExportService(session).reserve(
        requested_by_id=admin.id,
        period_from=date(2026, 7, 1),
        period_to=date(2026, 7, 31),
        expert_id=None,
        mode=ExportMode.NEW,
        affects_processing=True,
    )
    _, rows = await ExportService(session).load_rows(second.id)
    assert [row.inspection_id for row in rows] == [inspection.id]
    assert rows[0].modified_at is not None
    assert rows[0].comment == "Исправлено после выгрузки"


@pytest.mark.asyncio
async def test_pending_supplier_can_be_merged(session: AsyncSession) -> None:
    await seed_reference_data(session)
    user = await create_user(session)
    supplier_service = DirectoryService(session)
    pending = await supplier_service.resolve_manual_supplier("Гранада тест", user.id)
    target = await session.scalar(select(Supplier).where(Supplier.name == "Гранада ООО"))
    _, regular, _, project = await reference_entities(session)
    assert target and regular and project
    inspection = await InspectionService(session).create(
        InspectionInput(
            author_id=user.id,
            work_type=InspectionWorkType.EXPERT,
            inspection_date=date(2026, 7, 14),
            order_number_raw="ABC1",
            order_number_normalized="ABC1",
            supplier_id=pending.id,
            status_id=regular.id,
            project_id=project.id,
            rate=Decimal("1.00"),
            comment=None,
        )
    )
    await supplier_service.merge_supplier(pending.id, target.id)
    await session.commit()
    await session.refresh(inspection)
    assert inspection.supplier_id == target.id
    assert pending.status is SupplierStatus.INACTIVE
    assert pending.merged_into_id == target.id


@pytest.mark.asyncio
async def test_pending_supplier_can_be_rejected_without_losing_inspection(
    session: AsyncSession,
) -> None:
    await seed_reference_data(session)
    user = await create_user(session)
    supplier_service = DirectoryService(session)
    pending = await supplier_service.resolve_manual_supplier("Ошибочный поставщик", user.id)
    _, regular, _, project = await reference_entities(session)
    inspection = await InspectionService(session).create(
        InspectionInput(
            author_id=user.id,
            work_type=InspectionWorkType.EXPERT,
            inspection_date=date(2026, 7, 14),
            order_number_raw="REJECT1",
            order_number_normalized="REJECT1",
            supplier_id=pending.id,
            status_id=regular.id,
            project_id=project.id,
            rate=Decimal("1.00"),
            comment=None,
        )
    )

    await supplier_service.reject_supplier(pending.id)
    await session.commit()
    await session.refresh(inspection)

    assert pending.status is SupplierStatus.INACTIVE
    assert inspection.supplier_id == pending.id
    assert not await supplier_service.list_suppliers(status=SupplierStatus.PENDING)


@pytest.mark.asyncio
async def test_active_supplier_cannot_be_rejected(session: AsyncSession) -> None:
    await seed_reference_data(session)
    supplier = (await DirectoryService(session).list_suppliers())[0]

    with pytest.raises(DomainError, match="уже обработан"):
        await DirectoryService(session).reject_supplier(supplier.id)


@pytest.mark.asyncio
async def test_supplier_search_supports_pagination(session: AsyncSession) -> None:
    await seed_reference_data(session)
    service = DirectoryService(session)
    first = await service.search_suppliers("ООО", limit=5, offset=0)
    second = await service.search_suppliers("ООО", limit=5, offset=5)

    assert len(first) == 5
    assert len(second) == 5
    assert {item.id for item in first}.isdisjoint(item.id for item in second)


@pytest.mark.asyncio
async def test_user_repository_paginates_report_experts(session: AsyncSession) -> None:
    for index in range(25):
        await create_user(session, telegram_id=1_000 + index)
    service = UserService(session)
    first = await service.list_for_reports(limit=10, offset=0)
    second = await service.list_for_reports(limit=10, offset=10)

    assert len(first) == 10
    assert len(second) == 10
    assert {item.id for item in first}.isdisjoint(item.id for item in second)


@pytest.mark.asyncio
async def test_user_without_role_cannot_be_unblocked(session: AsyncSession) -> None:
    user = User(
        telegram_id=777,
        full_name="Новая заявка",
        requested_role=UserRole.EXPERT,
        role=None,
        status=UserStatus.BLOCKED,
    )
    session.add(user)
    await session.flush()

    with pytest.raises(ValueError, match="назначьте пользователю роль"):
        await UserService(session).set_blocked(user.id, False)


@pytest.mark.asyncio
async def test_bootstrap_admin_name_syncs_from_telegram(session: AsyncSession) -> None:
    service = UserService(session)
    await service.bootstrap_admins([999])
    admin = await service.sync_telegram_profile(
        telegram_id=999, username="kseniia", telegram_name="Ксения"
    )
    await session.commit()

    assert admin is not None
    assert admin.full_name == "Ксения"
    assert admin.username == "kseniia"


@pytest.mark.asyncio
async def test_specialist_creates_inspection_without_fuel(session: AsyncSession) -> None:
    await seed_reference_data(session)
    specialist = await create_user(session)
    specialist.role = UserRole.SPECIALIST
    _, regular, _, _ = await reference_entities(session)

    inspection = await create_inspection(session, specialist, order="SPECIALIST1", status=regular)
    await session.commit()

    assert inspection.id is not None
    assert inspection.fuel is None


@pytest.mark.asyncio
async def test_new_inspection_never_stores_fuel(session: AsyncSession) -> None:
    await seed_reference_data(session)
    expert = await create_user(session)
    _, regular, _, _ = await reference_entities(session)

    inspection = await create_inspection(
        session, expert, order="EXPERTNOFUEL1", status=regular
    )
    assert inspection.fuel is None


@pytest.mark.asyncio
async def test_specialist_is_available_in_report_filter(session: AsyncSession) -> None:
    specialist = await create_user(session, telegram_id=778)
    specialist.role = UserRole.SPECIALIST
    await session.flush()

    users = await UserService(session).list_for_reports()

    assert specialist.id in {user.id for user in users}


@pytest.mark.asyncio
async def test_inactive_directory_value_cannot_be_used(session: AsyncSession) -> None:
    await seed_reference_data(session)
    user = await create_user(session)
    supplier, regular, _, project = await reference_entities(session)
    project.active = False

    with pytest.raises(DomainError, match="Проект деактивирован"):
        await InspectionService(session).create(
            InspectionInput(
                author_id=user.id,
                work_type=InspectionWorkType.EXPERT,
                inspection_date=date(2026, 7, 14),
                order_number_raw="INACTIVE1",
                order_number_normalized="INACTIVE1",
                supplier_id=supplier.id,
                status_id=regular.id,
                project_id=project.id,
                rate=Decimal("1.00"),
                comment=None,
            )
        )


@pytest.mark.asyncio
async def test_employee_exports_are_personal_and_do_not_process_rows(
    session: AsyncSession,
) -> None:
    await seed_reference_data(session)
    expert = await create_user(session, telegram_id=199)
    specialist = await create_user(session, telegram_id=200)
    specialist.role = UserRole.SPECIALIST
    admin = await create_user(session, telegram_id=201)
    admin.role = UserRole.ADMIN
    _, regular, _, _ = await reference_entities(session)
    expert_record = await create_inspection(
        session, expert, order="EXPERTPERSONAL1", status=regular
    )
    specialist_record = await create_inspection(
        session, specialist, order="SPECIALISTPERSONAL1", status=regular
    )
    await session.commit()

    service = ExportService(session)
    for employee, expected_record in (
        (expert, expert_record),
        (specialist, specialist_record),
    ):
        batch = await service.reserve(
            requested_by_id=employee.id,
            period_from=date(2026, 7, 1),
            period_to=date(2026, 7, 31),
            expert_id=None,
            mode=ExportMode.FULL,
            affects_processing=False,
        )
        assert batch.expert_id == employee.id
        _, personal_rows = await service.load_rows(batch.id)
        assert [row.inspection_id for row in personal_rows] == [expected_record.id]
        await service.mark_sent(
            batch.id, telegram_chat_id=employee.telegram_id, telegram_message_id=1
        )
        await session.commit()

    admin_batch = await service.reserve(
        requested_by_id=admin.id,
        period_from=date(2026, 7, 1),
        period_to=date(2026, 7, 31),
        expert_id=None,
        mode=ExportMode.NEW,
        affects_processing=True,
    )
    _, admin_rows = await service.load_rows(admin_batch.id)
    assert {row.inspection_id for row in admin_rows} == {
        expert_record.id,
        specialist_record.id,
    }


@pytest.mark.asyncio
async def test_employee_cannot_retry_legacy_unscoped_export(
    session: AsyncSession,
) -> None:
    await seed_reference_data(session)
    specialist = await create_user(session, telegram_id=202)
    specialist.role = UserRole.SPECIALIST
    _, regular, _, _ = await reference_entities(session)
    await create_inspection(session, specialist, order="LEGACYEXPORT1", status=regular)
    await session.commit()

    service = ExportService(session)
    batch = await service.reserve(
        requested_by_id=specialist.id,
        period_from=date(2026, 7, 1),
        period_to=date(2026, 7, 31),
        expert_id=None,
        mode=ExportMode.FULL,
        affects_processing=False,
    )
    await service.mark_failed(batch.id, "old failure")
    batch.expert_id = None
    await session.commit()

    with pytest.raises(DomainError, match="не ограничена вашими записями"):
        await service.prepare_retry(batch.id, specialist.id)


@pytest.mark.asyncio
async def test_failed_reservation_releases_rows(session: AsyncSession) -> None:
    await seed_reference_data(session)
    user = await create_user(session)
    user.role = UserRole.ADMIN
    _, regular, _, _ = await reference_entities(session)
    await create_inspection(session, user, order="ABC3", status=regular)
    await session.commit()

    service = ExportService(session)
    first = await service.reserve(
        requested_by_id=user.id,
        period_from=date(2026, 7, 1),
        period_to=date(2026, 7, 31),
        expert_id=None,
        mode=ExportMode.NEW,
        affects_processing=True,
    )
    await service.mark_failed(first.id, "telegram error")
    await session.commit()
    failed_batch, saved_rows = await service.load_rows(first.id)
    assert failed_batch.id == first.id
    assert [row.order_number for row in saved_rows] == ["ABC3"]
    second = await service.reserve(
        requested_by_id=user.id,
        period_from=date(2026, 7, 1),
        period_to=date(2026, 7, 31),
        expert_id=None,
        mode=ExportMode.NEW,
        affects_processing=True,
    )
    assert isinstance(second, ExportBatch)


@pytest.mark.asyncio
async def test_failed_batch_retry_preserves_original_composition(session: AsyncSession) -> None:
    await seed_reference_data(session)
    admin = await create_user(session)
    admin.role = UserRole.ADMIN
    _, regular, _, _ = await reference_entities(session)
    await create_inspection(session, admin, order="RETRY1", status=regular)
    await session.commit()

    service = ExportService(session)
    failed = await service.reserve(
        requested_by_id=admin.id,
        period_from=date(2026, 7, 1),
        period_to=date(2026, 7, 31),
        expert_id=None,
        mode=ExportMode.NEW,
        affects_processing=True,
    )
    await service.mark_failed(failed.id, "telegram error")
    await session.commit()

    await create_inspection(session, admin, order="RETRY2", status=regular)
    await session.commit()
    retry = await service.prepare_retry(failed.id, admin.id)
    _, rows = await service.load_rows(retry.id)

    assert retry.id == failed.id
    assert retry.error_message is None
    assert [row.order_number for row in rows] == ["RETRY1"]


@pytest.mark.asyncio
async def test_full_admin_export_reserves_rows_until_finished(session: AsyncSession) -> None:
    await seed_reference_data(session)
    admin = await create_user(session)
    admin.role = UserRole.ADMIN
    _, regular, _, _ = await reference_entities(session)
    await create_inspection(session, admin, order="RESERVE1", status=regular)
    await session.commit()

    service = ExportService(session)
    await service.reserve(
        requested_by_id=admin.id,
        period_from=date(2026, 7, 1),
        period_to=date(2026, 7, 31),
        expert_id=None,
        mode=ExportMode.FULL,
        affects_processing=True,
    )

    with pytest.raises(EmptyExport):
        await service.reserve(
            requested_by_id=admin.id,
            period_from=date(2026, 7, 1),
            period_to=date(2026, 7, 31),
            expert_id=None,
            mode=ExportMode.FULL,
            affects_processing=True,
        )


@pytest.mark.asyncio
async def test_specialist_cannot_mark_rows_processed(session: AsyncSession) -> None:
    await seed_reference_data(session)
    specialist = await create_user(session)
    specialist.role = UserRole.SPECIALIST

    with pytest.raises(DomainError, match="нет права"):
        await ExportService(session).reserve(
            requested_by_id=specialist.id,
            period_from=date(2026, 7, 1),
            period_to=date(2026, 7, 31),
            expert_id=None,
            mode=ExportMode.NEW,
            affects_processing=True,
        )
