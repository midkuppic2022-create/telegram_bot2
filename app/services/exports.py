from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import (
    ExportGroup,
    ExportMode,
    ExportState,
    InspectionScenario,
    InspectionWorkType,
    UserRole,
    UserStatus,
)
from app.errors import DomainError, EmptyExport, EntityNotFound
from app.models import ExportBatch, User
from app.repositories.exports import ExportOrderRecord, ExportRepository
from app.repositories.users import UserRepository

EXPORT_ADVISORY_LOCK = 740_260_714


@dataclass(slots=True, frozen=True)
class ExportRow:
    inspection_id: int
    group_number: int
    position: int
    expert_name: str
    inspection_date: date
    order_number: str
    normalized_order: str
    normalized_orders: tuple[str, ...]
    work_type: InspectionWorkType
    supplier_name: str | None
    status_name: str
    project_name: str
    export_group: ExportGroup
    rate: Decimal
    comment: str | None
    created_at: datetime | None
    modified_at: datetime | None
    processed_at: datetime | None
    is_duplicate: bool


def _status_for_export(
    scenario: InspectionScenario,
    stored_name: str,
    work_type: InspectionWorkType,
    vehicle_number: str | None,
) -> str:
    if scenario is InspectionScenario.NEXT:
        if stored_name.casefold() != "далее":
            return stored_name
        if work_type is InspectionWorkType.EXPERT:
            return "контроль отгрузки"
        return "-"
    if scenario is InspectionScenario.SHIFT:
        return "смена"
    if scenario is InspectionScenario.SAME_VEHICLE:
        label = (
            "отгружены в одну авто"
            if work_type is InspectionWorkType.SPECIALIST
            else "отгружались в одной авто"
        )
        return f"{label} {vehicle_number}" if vehicle_number else label
    return stored_name


class ExportService:
    def __init__(self, session: AsyncSession, *, reservation_minutes: int = 15) -> None:
        self.repository = ExportRepository(session)
        self.users = UserRepository(session)
        self.reservation_minutes = reservation_minutes

    async def cleanup_stale_reservations(self) -> None:
        threshold = datetime.now(UTC) - timedelta(minutes=self.reservation_minutes)
        await self.repository.cleanup_stale(threshold)

    @staticmethod
    def _validate_requester(
        requester: User | None, *, affects_processing: bool
    ) -> User:
        if requester is None:
            raise EntityNotFound("Пользователь не найден.")
        allowed_roles = (
            {UserRole.ADMIN}
            if affects_processing
            else {UserRole.EXPERT, UserRole.SPECIALIST, UserRole.ADMIN}
        )
        if requester.status is not UserStatus.ACTIVE or requester.role not in allowed_roles:
            raise DomainError("У пользователя нет права выполнять эту выгрузку.")
        return requester

    async def reserve(
        self,
        *,
        requested_by_id: int,
        period_from: date,
        period_to: date,
        expert_id: int | None,
        mode: ExportMode,
        affects_processing: bool,
    ) -> ExportBatch:
        if period_from > period_to:
            raise ValueError("Invalid export period")
        requester = self._validate_requester(
            await self.users.get(requested_by_id),
            affects_processing=affects_processing,
        )
        if requester.role is not UserRole.ADMIN:
            # Personal exports are always constrained server-side, regardless of UI input.
            expert_id = requester.id
        await self.cleanup_stale_reservations()
        if affects_processing:
            await self.repository.lock_processing_exports(EXPORT_ADVISORY_LOCK)
        inspection_ids = await self.repository.candidate_inspection_ids(
            period_from=period_from,
            period_to=period_to,
            expert_id=expert_id,
            mode=mode,
            affects_processing=affects_processing,
        )
        if not inspection_ids:
            raise EmptyExport("За выбранный период нет подходящих строк для выгрузки.")
        return await self.repository.create_batch(
            requested_by_id=requested_by_id,
            period_from=period_from,
            period_to=period_to,
            expert_id=expert_id,
            mode=mode,
            affects_processing=affects_processing,
            inspection_ids=inspection_ids,
            attempted_at=datetime.now(UTC),
        )

    async def list_failed_for_user(
        self, requested_by_id: int, *, limit: int = 5
    ) -> list[ExportBatch]:
        return await self.repository.list_failed_for_user(requested_by_id, limit=limit)

    async def prepare_retry(self, batch_id: int, requested_by_id: int) -> ExportBatch:
        batch = await self.repository.get_batch(batch_id)
        if batch is None:
            raise EntityNotFound("Пакет выгрузки не найден.")
        if batch.requested_by_id != requested_by_id:
            raise DomainError("Можно повторить только собственную выгрузку.")
        requester = self._validate_requester(
            await self.users.get(requested_by_id),
            affects_processing=batch.affects_processing,
        )
        if requester.role is not UserRole.ADMIN and batch.expert_id != requester.id:
            raise DomainError(
                "Эта старая выгрузка не ограничена вашими записями. Создайте новую."
            )
        if batch.state is not ExportState.FAILED:
            raise DomainError("Этот пакет уже обрабатывается или был успешно отправлен.")
        await self.cleanup_stale_reservations()
        if batch.affects_processing:
            await self.repository.lock_processing_exports(EXPORT_ADVISORY_LOCK)
            if await self.repository.has_conflicting_reservation(batch.id):
                raise DomainError(
                    "Часть строк сейчас зарезервирована другой выгрузкой. Повторите позже."
                )
        batch.state = ExportState.PREPARING
        batch.error_message = None
        batch.attempted_at = datetime.now(UTC)
        await self.repository.flush()
        return batch

    async def load_rows(self, batch_id: int) -> tuple[ExportBatch, list[ExportRow]]:
        batch = await self.repository.get_batch(batch_id)
        if batch is None:
            raise EntityNotFound("Пакет выгрузки не найден.")
        records = await self.repository.load_records(batch_id)
        order_numbers = await self.repository.load_order_numbers(
            [row.inspection_id for row in records]
        )
        keys = {
            (order.normalized, row.work_type)
            for row in records
            for order in order_numbers.get(
                row.inspection_id,
                [ExportOrderRecord(row.order_number, row.normalized_order, row.supplier_name)],
            )
        }
        duplicates = await self.repository.duplicate_order_keys(keys)

        rows: list[ExportRow] = []
        for group_number, row in enumerate(records, start=1):
            inspection_orders = order_numbers.get(
                row.inspection_id,
                [ExportOrderRecord(row.order_number, row.normalized_order, row.supplier_name)],
            )
            normalized_orders = tuple(item.normalized for item in inspection_orders)
            processed_at = row.processed_at
            if batch.affects_processing and processed_at is None:
                processed_at = batch.attempted_at
            primary_status = _status_for_export(
                row.scenario,
                row.status_name,
                row.work_type,
                row.vehicle_number,
            )
            for position, order in enumerate(inspection_orders):
                primary = position == 0
                rows.append(
                    ExportRow(
                        inspection_id=row.inspection_id,
                        group_number=group_number,
                        position=position,
                        expert_name=row.expert_name,
                        inspection_date=row.inspection_date,
                        order_number=order.raw,
                        normalized_order=order.normalized,
                        normalized_orders=normalized_orders,
                        work_type=row.work_type,
                        supplier_name=order.supplier_name or row.supplier_name,
                        status_name=primary_status if primary else "-",
                        project_name=row.project_name,
                        export_group=row.export_group,
                        rate=row.rate if primary else Decimal("0.00"),
                        comment=row.comment if primary else None,
                        created_at=row.created_at,
                        modified_at=row.updated_at,
                        processed_at=processed_at,
                        is_duplicate=(order.normalized, row.work_type) in duplicates,
                    )
                )
        return batch, rows

    async def mark_sent(
        self,
        batch_id: int,
        *,
        telegram_chat_id: int,
        telegram_message_id: int,
    ) -> ExportBatch:
        batch = await self.repository.get_batch(batch_id)
        if batch is None:
            raise EntityNotFound("Пакет выгрузки не найден.")
        batch.state = ExportState.SENT
        batch.sent_at = datetime.now(UTC)
        batch.telegram_chat_id = telegram_chat_id
        batch.telegram_message_id = telegram_message_id
        batch.error_message = None
        await self.repository.flush()
        return batch

    async def mark_failed(self, batch_id: int, error: str) -> ExportBatch:
        batch = await self.repository.get_batch(batch_id)
        if batch is None:
            raise EntityNotFound("Пакет выгрузки не найден.")
        batch.state = ExportState.FAILED
        batch.error_message = error[:500]
        await self.repository.flush()
        return batch
