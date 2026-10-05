from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import (
    InspectionScenario,
    InspectionWorkType,
    ProjectCode,
    ReconciliationState,
    UserRole,
    UserStatus,
)
from app.errors import DomainError
from app.models import (
    InspectionRevision,
    InspectionStatus,
    Project,
    ReferenceUpload,
    User,
)
from app.seed import seed_reference_data
from app.services.inspections import (
    InspectionInput,
    InspectionService,
    InspectionUpdateInput,
    OrderNumberInput,
)
from app.services.reconciliation import ReconciliationService, parse_reference_workbook


def workbook_bytes(rows: list[tuple[object, object]], *, extra_value: object = None) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Заказ", "Поставщик"])
    for order, supplier in rows:
        sheet.append([order, supplier])
    if extra_value is not None:
        sheet.cell(2, 3, extra_value)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


async def create_user(
    session: AsyncSession,
    telegram_id: int,
    role: UserRole,
) -> User:
    user = User(
        telegram_id=telegram_id,
        full_name=f"Пользователь {telegram_id}",
        requested_role=role,
        role=role,
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_reference_parser_normalizes_numbers_and_collapses_duplicates() -> None:
    parsed = parse_reference_workbook(
        workbook_bytes(
            [
                (2300098844362, "Поставщик 1"),
                (" abc123 ", "Поставщик 2"),
                ("ABC123", "  Поставщик   2 "),
                ("7.001E+5", None),
            ]
        )
    )

    assert [(row.normalized, row.supplier_name) for row in parsed.rows] == [
        ("2300098844362", "Поставщик 1"),
        ("ABC123", "Поставщик 2"),
        ("700100", None),
    ]
    assert parsed.warning_count == 1


def test_reference_parser_ignores_supplier_spacing_and_quote_styles() -> None:
    parsed = parse_reference_workbook(
        workbook_bytes(
            [
                ("A1", 'ООО"ФРУКТОВЫЙСАД"'),
                ("a1", 'ООО "ФРУКТОВЫЙ САД"'),
                ("B2", 'ООО"СХП"РАССВЕТ"'),
                ("b2", 'ООО "СХП "РАССВЕТ"'),
                ("C3", "ООО 'ЗЕЛЁНАЯ ДОЛИНА'"),
                ("c3", 'ооо «зелёная долина»'),
            ]
        )
    )

    assert [(row.normalized, row.supplier_name) for row in parsed.rows] == [
        ("A1", 'ООО"ФРУКТОВЫЙСАД"'),
        ("B2", 'ООО"СХП"РАССВЕТ"'),
        ("C3", "ООО 'ЗЕЛЁНАЯ ДОЛИНА'"),
    ]


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (
            workbook_bytes([("ABC1", "Первый"), ("abc1", "Второй")]),
            "разные поставщики",
        ),
        (workbook_bytes([("ABC1", "Первый")], extra_value="лишнее"), "две"),
        (workbook_bytes([(True, "Первый")]), "некорректный номер"),
    ],
)
def test_reference_parser_rejects_invalid_file(content: bytes, message: str) -> None:
    with pytest.raises(DomainError, match=message):
        parse_reference_workbook(content)


@pytest.mark.asyncio
async def test_reference_versions_are_atomic_and_resolvable(session: AsyncSession) -> None:
    admin = await create_user(session, 8100, UserRole.ADMIN)
    admin_id = admin.id
    service = ReconciliationService(session)

    assert (await service.resolve("A1")).state is ReconciliationState.NO_FILE
    first = await service.import_xlsx(
        content=workbook_bytes([("A1", "Поставщик А")]),
        filename="first.xlsx",
        uploaded_by=admin,
    )
    await session.commit()
    first_match = await service.resolve("A1")
    assert first_match.state is ReconciliationState.FOUND
    assert first_match.supplier_name == "Поставщик А"
    assert (await service.resolve("MISSING1")).state is ReconciliationState.MISSING

    with pytest.raises(DomainError, match="разные поставщики"):
        await service.import_xlsx(
            content=workbook_bytes([("A1", "Один"), ("a1", "Другой")]),
            filename="broken.xlsx",
            uploaded_by=admin,
        )
    await session.rollback()
    assert (await service.get_active_upload()).id == first.id
    admin = await session.get(User, admin_id)
    assert admin is not None

    second = await service.import_xlsx(
        content=workbook_bytes([("A1", "Поставщик Б"), ("B2", None)]),
        filename="second.xlsx",
        uploaded_by=admin,
    )
    await session.commit()

    uploads = list((await session.scalars(select(ReferenceUpload).order_by(ReferenceUpload.id))).all())
    assert [(item.id, item.active) for item in uploads] == [
        (first.id, False),
        (second.id, True),
    ]
    assert second.warning_count == 1
    assert (await service.resolve("A1")).supplier_name == "Поставщик Б"


@pytest.mark.asyncio
async def test_only_admin_can_upload_reference_file(session: AsyncSession) -> None:
    expert = await create_user(session, 8101, UserRole.EXPERT)
    with pytest.raises(DomainError, match="только администратор"):
        await ReconciliationService(session).import_xlsx(
            content=workbook_bytes([("A1", "Поставщик")]),
            filename="reference.xlsx",
            uploaded_by=expert,
        )


@pytest.mark.asyncio
async def test_supplier_snapshot_survives_new_upload_and_changes_with_order(
    session: AsyncSession,
) -> None:
    await seed_reference_data(session)
    admin = await create_user(session, 8102, UserRole.ADMIN)
    specialist = await create_user(session, 8103, UserRole.SPECIALIST)
    status = await session.scalar(
        select(InspectionStatus).where(InspectionStatus.code == InspectionScenario.NEXT)
    )
    project = await session.scalar(
        select(Project).where(Project.code == ProjectCode.THUNDER_AGRO_MAF)
    )
    assert status and project
    reconciliation = ReconciliationService(session)
    first_upload = await reconciliation.import_xlsx(
        content=workbook_bytes([("OLD1", "Старый поставщик")]),
        filename="day-1.xlsx",
        uploaded_by=admin,
    )
    await session.commit()
    first_match = await reconciliation.resolve("OLD1")

    inspection = await InspectionService(session).create(
        InspectionInput(
            author_id=specialist.id,
            work_type=InspectionWorkType.SPECIALIST,
            inspection_date=date(2026, 7, 1),
            order_number_raw="OLD1",
            order_number_normalized="OLD1",
            supplier_id=None,
            status_id=status.id,
            project_id=project.id,
            rate=Decimal("1000.00"),
            comment=None,
            primary_supplier_name=first_match.supplier_name,
            primary_reconciliation_state=first_match.state,
            primary_reference_upload_id=first_match.upload_id,
        )
    )
    await session.commit()

    await reconciliation.import_xlsx(
        content=workbook_bytes(
            [("OLD1", "Новый поставщик"), ("NEW2", "Поставщик нового заказа")]
        ),
        filename="day-2.xlsx",
        uploaded_by=admin,
    )
    await session.commit()
    await session.refresh(inspection, ["order_numbers"])
    assert inspection.order_numbers[0].supplier_name_snapshot == "Старый поставщик"
    assert inspection.order_numbers[0].reference_upload_id == first_upload.id

    new_match = await reconciliation.resolve("NEW2")
    updated = await InspectionService(session).update(
        InspectionUpdateInput(
            inspection_id=inspection.id,
            changed_by_id=specialist.id,
            inspection_date=inspection.inspection_date,
            order_number_raw="NEW2",
            order_number_normalized="NEW2",
            supplier_id=None,
            status_id=status.id,
            project_id=project.id,
            rate=inspection.rate,
            comment="номер исправлен",
            primary_supplier_name=new_match.supplier_name,
            primary_reconciliation_state=new_match.state,
            primary_reference_upload_id=new_match.upload_id,
        )
    )
    await session.commit()

    assert updated.order_numbers[0].supplier_name_snapshot == "Поставщик нового заказа"
    revision = await session.scalar(select(InspectionRevision))
    assert revision is not None
    assert revision.before_data["orders"][0]["supplier_name"] == "Старый поставщик"


@pytest.mark.asyncio
async def test_each_order_keeps_its_own_supplier_snapshot(session: AsyncSession) -> None:
    await seed_reference_data(session)
    specialist = await create_user(session, 8104, UserRole.SPECIALIST)
    status = await session.scalar(
        select(InspectionStatus).where(
            InspectionStatus.code == InspectionScenario.SAME_VEHICLE
        )
    )
    project = await session.scalar(
        select(Project).where(Project.code == ProjectCode.THUNDER_SELF_PICKUP)
    )
    assert status and project

    inspection = await InspectionService(session).create(
        InspectionInput(
            author_id=specialist.id,
            work_type=InspectionWorkType.SPECIALIST,
            inspection_date=date(2026, 7, 2),
            order_number_raw="CAR1",
            order_number_normalized="CAR1",
            supplier_id=None,
            status_id=status.id,
            project_id=project.id,
            rate=Decimal("2500.00"),
            comment=None,
            primary_supplier_name="Поставщик 1",
            primary_reconciliation_state=ReconciliationState.FOUND,
            primary_reference_upload_id=None,
            related_orders=(
                OrderNumberInput(
                    "CAR2",
                    "CAR2",
                    "Поставщик 2",
                    ReconciliationState.FOUND,
                    None,
                ),
            ),
        )
    )
    await session.commit()

    assert [item.supplier_name_snapshot for item in inspection.order_numbers] == [
        "Поставщик 1",
        "Поставщик 2",
    ]
    assert await session.scalar(select(func.count(ReferenceUpload.id))) == 0
