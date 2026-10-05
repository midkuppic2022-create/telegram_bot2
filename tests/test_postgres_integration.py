import asyncio
import os
from datetime import date
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.enums import (
    InspectionScenario,
    InspectionWorkType,
    ProjectCode,
    SupplierStatus,
    UserRole,
    UserStatus,
)
from app.errors import DuplicateOrderNotAllowed
from app.models import Inspection, InspectionOrderNumber, InspectionStatus, Project, Supplier, User
from app.seed import seed_reference_data
from app.services.inspections import InspectionInput, InspectionService
from app.services.users import UserService

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not configured for PostgreSQL integration tests",
)


async def seed_legacy_database() -> None:
    assert TEST_DATABASE_URL is not None
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as connection:
        user_id = (
            await connection.execute(
                text(
                    "INSERT INTO users "
                    "(telegram_id, full_name, requested_role, role, status) "
                    "VALUES (8999, 'Исторический эксперт', 'expert', 'expert', 'active') "
                    "RETURNING id"
                )
            )
        ).scalar_one()
        supplier_id = (
            await connection.execute(
                text(
                    "INSERT INTO suppliers (name, normalized_name, status) "
                    "VALUES ('Исторический поставщик', 'исторический поставщик', 'active') "
                    "RETURNING id"
                )
            )
        ).scalar_one()
        status_id = (
            await connection.execute(
                text(
                    "INSERT INTO inspection_statuses "
                    "(name, normalized_name, allows_duplicate, active, supports_related_orders) "
                    "VALUES ('осмотр', 'осмотр', FALSE, TRUE, FALSE) RETURNING id"
                )
            )
        ).scalar_one()
        project_ids = []
        for name, normalized, group in (
            ("АК", "ак", "АК"),
            ("АК, арбуз", "ак, арбуз", "АК"),
            ("СВ, арбуз", "св, арбуз", "СВ"),
            ("СВ", "св", "СВ"),
            ("Х5 СВ", "х5 св", "Х5"),
            ("Х5 ТехМП", "х5 техмп", "Х5"),
            ("Старый отдельный проект", "старый отдельный проект", "СВ"),
        ):
            project_ids.append(
                (
                    await connection.execute(
                        text(
                            "INSERT INTO projects "
                            "(name, normalized_name, export_group, active) "
                            "VALUES (:name, :normalized, :group, TRUE) RETURNING id"
                        ),
                        {"name": name, "normalized": normalized, "group": group},
                    )
                ).scalar_one()
            )
        for index, project_id in enumerate(project_ids, start=1):
            order = f"LEGACYPG{index}"
            inspection_id = (
                await connection.execute(
                    text(
                        "INSERT INTO inspections "
                        "(author_id, inspection_date, order_number_raw, "
                        "order_number_normalized, supplier_id, status_id, project_id, "
                        "rate, fuel, work_type) VALUES "
                        "(:author_id, '2026-07-01', :order, :order, :supplier_id, "
                        ":status_id, :project_id, 700, NULL, 'expert') RETURNING id"
                    ),
                    {
                        "author_id": user_id,
                        "order": order,
                        "supplier_id": supplier_id,
                        "status_id": status_id,
                        "project_id": project_id,
                    },
                )
            ).scalar_one()
            await connection.execute(
                text(
                    "INSERT INTO inspection_order_numbers "
                    "(inspection_id, order_number_raw, order_number_normalized, position) "
                    "VALUES (:inspection_id, :order, :order, 0)"
                ),
                {"inspection_id": inspection_id, "order": order},
            )
    await engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def migrated_postgres_database():
    if not TEST_DATABASE_URL:
        yield
        return
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    config = Config("alembic.ini")
    command.downgrade(config, "base")
    command.upgrade(config, "0003_work_types_orders_edits")
    asyncio.run(seed_legacy_database())
    command.upgrade(config, "head")
    try:
        yield
    finally:
        command.downgrade(config, "base")
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


@pytest.mark.asyncio
async def test_postgres_migration_preserves_historical_orders_and_projects() -> None:
    assert TEST_DATABASE_URL is not None
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        inspections = list(
            (
                await session.scalars(
                    select(Inspection)
                    .where(Inspection.order_number_normalized.like("LEGACYPG%"))
                    .order_by(Inspection.id)
                )
            ).all()
        )
        orders = list(
            (
                await session.scalars(
                    select(InspectionOrderNumber)
                    .where(InspectionOrderNumber.order_number_normalized.like("LEGACYPG%"))
                    .order_by(InspectionOrderNumber.id)
                )
            ).all()
        )
        projects = list(
            (
                await session.scalars(
                    select(Project).where(Project.code == ProjectCode.THUNDER_WATERMELONS)
                )
            ).all()
        )

        assert len(inspections) == 7
        project_codes = [
            (await session.scalar(select(Project.code).where(Project.id == item.project_id)))
            for item in inspections
        ]
        assert project_codes == [
            ProjectCode.THUNDER_AGRO_MAF,
            ProjectCode.THUNDER_WATERMELONS,
            ProjectCode.THUNDER_WATERMELONS,
            ProjectCode.THUNDER_SELF_PICKUP,
            ProjectCode.X5_SELF_PICKUP,
            ProjectCode.X5_TECHMP_RVI,
            None,
        ]
        assert len(projects) == 1
        assert projects[0].name == "Тандер, арбузы"
        assert [item.supplier_name_snapshot for item in orders] == ["Исторический поставщик"] * 7
        assert all(item.reconciliation_state.value == "legacy" for item in orders)
        legacy_project = await session.scalar(
            select(Project).where(Project.normalized_name == "старый отдельный проект")
        )
        assert legacy_project is not None
        assert legacy_project.code is None
        assert legacy_project.active is False
        deletion_columns = set(
            (
                await session.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'inspections' "
                        "AND column_name IN ('deleted_at', 'deleted_by_id')"
                    )
                )
            ).all()
        )
        assert deletion_columns == {"deleted_at", "deleted_by_id"}
        notification_columns = set(
            (
                await session.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'registration_admin_notifications'"
                    )
                )
            ).all()
        )
        assert notification_columns == {
            "id",
            "user_id",
            "admin_chat_id",
            "telegram_message_id",
            "created_at",
        }
    await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_migration_and_concurrent_duplicate_lock() -> None:
    assert TEST_DATABASE_URL is not None
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        await seed_reference_data(session)
        user = User(
            telegram_id=9_001,
            full_name="Конкурентный эксперт",
            requested_role=UserRole.EXPERT,
            role=UserRole.EXPERT,
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        await session.flush()
        supplier = await session.scalar(
            select(Supplier).where(Supplier.status == SupplierStatus.ACTIVE)
        )
        status = await session.scalar(
            select(InspectionStatus).where(
                InspectionStatus.code == InspectionScenario.INSPECTION_STOP
            )
        )
        project = await session.scalar(
            select(Project).where(Project.code == ProjectCode.THUNDER_AGRO_MAF)
        )
        assert supplier and status and project
        ids = user.id, supplier.id, status.id, project.id
        await session.commit()

    async def save_same_order() -> bool:
        async with factory() as session:
            data = InspectionInput(
                author_id=ids[0],
                work_type=InspectionWorkType.EXPERT,
                inspection_date=date(2026, 7, 14),
                order_number_raw="CONCURRENT1",
                order_number_normalized="CONCURRENT1",
                supplier_id=ids[1],
                status_id=ids[2],
                project_id=ids[3],
                rate=Decimal("1.00"),
                comment=None,
            )
            try:
                await InspectionService(session).create(data)
                await session.commit()
                return True
            except DuplicateOrderNotAllowed:
                await session.rollback()
                return False

    results = await asyncio.gather(save_same_order(), save_same_order())
    assert sorted(results) == [False, True]
    async with factory() as session:
        await InspectionService(session).create(
            InspectionInput(
                author_id=ids[0],
                work_type=InspectionWorkType.EXPERT,
                inspection_date=date(2026, 7, 14),
                order_number_raw="NOSUPPLIERPG1",
                order_number_normalized="NOSUPPLIERPG1",
                supplier_id=None,
                status_id=ids[2],
                project_id=ids[3],
                rate=Decimal("1.00"),
                comment=None,
            )
        )
        await session.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_registration_request_can_only_be_decided_once() -> None:
    assert TEST_DATABASE_URL is not None
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        pending = User(
            telegram_id=9_099,
            full_name="Конкурентная заявка",
            requested_role=UserRole.EXPERT,
            status=UserStatus.PENDING,
        )
        session.add(pending)
        await session.commit()
        pending_id = pending.id

    async def approve(role: UserRole) -> UserRole | None:
        async with factory() as session:
            try:
                await UserService(session).approve(pending_id, role)
                await session.commit()
            except ValueError:
                await session.rollback()
                return None
            return role

    results = await asyncio.gather(
        approve(UserRole.EXPERT),
        approve(UserRole.SPECIALIST),
    )
    assert sum(result is not None for result in results) == 1

    async with factory() as session:
        decided = await session.get(User, pending_id)
        assert decided is not None
        assert decided.status is UserStatus.ACTIVE
        assert decided.role in {UserRole.EXPERT, UserRole.SPECIALIST}

    await engine.dispose()
