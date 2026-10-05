from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import case, exists, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import (
    ExportGroup,
    ExportMode,
    ExportState,
    InspectionScenario,
    InspectionWorkType,
)
from app.models import (
    ExportBatch,
    ExportBatchItem,
    Inspection,
    InspectionOrderNumber,
    InspectionStatus,
    Project,
    Supplier,
    User,
)


@dataclass(slots=True, frozen=True)
class ExportRecord:
    inspection_id: int
    expert_name: str
    inspection_date: date
    order_number: str
    normalized_order: str
    work_type: InspectionWorkType
    supplier_name: str | None
    status_name: str
    project_name: str
    export_group: ExportGroup
    rate: Decimal
    comment: str | None
    created_at: datetime
    updated_at: datetime | None
    processed_at: datetime | None
    scenario: InspectionScenario
    vehicle_number: str | None


@dataclass(slots=True, frozen=True)
class ExportOrderRecord:
    raw: str
    normalized: str
    supplier_name: str | None


class ExportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def cleanup_stale(self, threshold: datetime) -> None:
        await self.session.execute(
            update(ExportBatch)
            .where(
                ExportBatch.state == ExportState.PREPARING,
                ExportBatch.attempted_at < threshold,
            )
            .values(
                state=ExportState.FAILED,
                error_message="Резервирование прервано или устарело.",
            )
        )

    async def lock_processing_exports(self, lock_id: int) -> None:
        if self.session.get_bind().dialect.name == "postgresql":
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": lock_id},
            )

    async def candidate_inspection_ids(
        self,
        *,
        period_from: date,
        period_to: date,
        expert_id: int | None,
        mode: ExportMode,
        affects_processing: bool,
    ) -> list[int]:
        statement = select(Inspection.id).where(
            Inspection.inspection_date.between(period_from, period_to)
        )
        if expert_id is not None:
            statement = statement.where(Inspection.author_id == expert_id)
        if affects_processing:
            already_reserved = exists(
                select(ExportBatchItem.inspection_id)
                .join(ExportBatch, ExportBatch.id == ExportBatchItem.batch_id)
                .where(
                    ExportBatchItem.inspection_id == Inspection.id,
                    ExportBatch.affects_processing.is_(True),
                    ExportBatch.state == ExportState.PREPARING,
                )
            )
            statement = statement.where(~already_reserved)
            if mode == ExportMode.NEW:
                current_version_sent = exists(
                    select(ExportBatchItem.inspection_id)
                    .join(ExportBatch, ExportBatch.id == ExportBatchItem.batch_id)
                    .where(
                        ExportBatchItem.inspection_id == Inspection.id,
                        ExportBatch.affects_processing.is_(True),
                        ExportBatch.state == ExportState.SENT,
                        or_(
                            Inspection.updated_at.is_(None),
                            ExportBatch.sent_at >= Inspection.updated_at,
                        ),
                    )
                )
                statement = statement.where(~current_version_sent)
        return list(
            (
                await self.session.scalars(
                    statement.order_by(Inspection.inspection_date, Inspection.id)
                )
            ).all()
        )

    async def create_batch(
        self,
        *,
        requested_by_id: int,
        period_from: date,
        period_to: date,
        expert_id: int | None,
        mode: ExportMode,
        affects_processing: bool,
        inspection_ids: list[int],
        attempted_at: datetime,
    ) -> ExportBatch:
        batch = ExportBatch(
            requested_by_id=requested_by_id,
            period_from=period_from,
            period_to=period_to,
            expert_id=expert_id,
            mode=mode,
            state=ExportState.PREPARING,
            affects_processing=affects_processing,
            attempted_at=attempted_at,
        )
        self.session.add(batch)
        await self.session.flush()
        self.session.add_all(
            ExportBatchItem(batch_id=batch.id, inspection_id=item_id, sort_order=index)
            for index, item_id in enumerate(inspection_ids, start=1)
        )
        await self.session.flush()
        await self.session.refresh(batch)
        return batch

    async def get_batch(self, batch_id: int) -> ExportBatch | None:
        return await self.session.get(ExportBatch, batch_id)

    async def list_failed_for_user(
        self, requested_by_id: int, *, limit: int = 5
    ) -> list[ExportBatch]:
        return list(
            (
                await self.session.scalars(
                    select(ExportBatch)
                    .where(
                        ExportBatch.requested_by_id == requested_by_id,
                        ExportBatch.state == ExportState.FAILED,
                    )
                    .order_by(ExportBatch.created_at.desc(), ExportBatch.id.desc())
                    .limit(limit)
                )
            ).all()
        )

    async def has_conflicting_reservation(self, batch_id: int) -> bool:
        source_items = (
            select(ExportBatchItem.inspection_id)
            .where(ExportBatchItem.batch_id == batch_id)
            .subquery()
        )
        result = await self.session.scalar(
            select(
                exists(
                    select(ExportBatchItem.inspection_id)
                    .join(ExportBatch, ExportBatch.id == ExportBatchItem.batch_id)
                    .where(
                        ExportBatchItem.inspection_id.in_(select(source_items.c.inspection_id)),
                        ExportBatchItem.batch_id != batch_id,
                        ExportBatch.affects_processing.is_(True),
                        ExportBatch.state == ExportState.PREPARING,
                    )
                )
            )
        )
        return bool(result)

    async def load_records(self, batch_id: int) -> list[ExportRecord]:
        latest_processed = (
            select(
                ExportBatchItem.inspection_id.label("inspection_id"),
                func.max(ExportBatch.sent_at).label("processed_at"),
            )
            .join(ExportBatch, ExportBatch.id == ExportBatchItem.batch_id)
            .where(
                ExportBatch.affects_processing.is_(True),
                ExportBatch.state == ExportState.SENT,
            )
            .group_by(ExportBatchItem.inspection_id)
            .subquery()
        )
        statement = (
            select(
                Inspection.id,
                User.full_name,
                Inspection.inspection_date,
                Inspection.order_number_raw,
                Inspection.order_number_normalized,
                Inspection.work_type,
                Supplier.name,
                InspectionStatus.name,
                Project.name,
                Project.export_group,
                Inspection.rate,
                Inspection.comment,
                Inspection.created_at,
                Inspection.updated_at,
                case(
                    (
                        or_(
                            Inspection.updated_at.is_(None),
                            latest_processed.c.processed_at >= Inspection.updated_at,
                        ),
                        latest_processed.c.processed_at,
                    ),
                    else_=None,
                ),
                Inspection.scenario,
                Inspection.vehicle_number,
            )
            .join(ExportBatchItem, ExportBatchItem.inspection_id == Inspection.id)
            .join(User, User.id == Inspection.author_id)
            .outerjoin(Supplier, Supplier.id == Inspection.supplier_id)
            .join(InspectionStatus, InspectionStatus.id == Inspection.status_id)
            .join(Project, Project.id == Inspection.project_id)
            .outerjoin(
                latest_processed, latest_processed.c.inspection_id == Inspection.id
            )
            .where(ExportBatchItem.batch_id == batch_id)
            .order_by(ExportBatchItem.sort_order)
        )
        return [ExportRecord(*row) for row in (await self.session.execute(statement)).all()]

    async def load_order_numbers(
        self, inspection_ids: list[int]
    ) -> dict[int, list[ExportOrderRecord]]:
        if not inspection_ids:
            return {}
        rows = (
            await self.session.execute(
                select(
                    InspectionOrderNumber.inspection_id,
                    InspectionOrderNumber.order_number_raw,
                    InspectionOrderNumber.order_number_normalized,
                    InspectionOrderNumber.supplier_name_snapshot,
                )
                .where(InspectionOrderNumber.inspection_id.in_(inspection_ids))
                .order_by(
                    InspectionOrderNumber.inspection_id,
                    InspectionOrderNumber.position,
                )
            )
        ).all()
        result: dict[int, list[ExportOrderRecord]] = {}
        for inspection_id, raw, normalized, supplier_name in rows:
            result.setdefault(int(inspection_id), []).append(
                ExportOrderRecord(str(raw), str(normalized), supplier_name)
            )
        return result

    async def duplicate_order_keys(
        self, keys: set[tuple[str, InspectionWorkType]]
    ) -> set[tuple[str, InspectionWorkType]]:
        if not keys:
            return set()
        normalized_orders = {key[0] for key in keys}
        work_types = {key[1] for key in keys}
        rows = (
            await self.session.execute(
                select(
                    InspectionOrderNumber.order_number_normalized,
                    Inspection.work_type,
                )
                .join(Inspection, Inspection.id == InspectionOrderNumber.inspection_id)
                .where(
                    InspectionOrderNumber.order_number_normalized.in_(normalized_orders),
                    Inspection.work_type.in_(work_types),
                )
                .group_by(
                    InspectionOrderNumber.order_number_normalized,
                    Inspection.work_type,
                )
                .having(func.count(Inspection.id) > 1)
            )
        ).all()
        return {
            (str(normalized), work_type)
            for normalized, work_type in rows
            if (str(normalized), work_type) in keys
        }

    async def flush(self) -> None:
        await self.session.flush()
