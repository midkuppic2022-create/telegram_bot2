from datetime import date

from sqlalchemy import exists, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.enums import ExportState, InspectionWorkType
from app.models import (
    ExportBatch,
    ExportBatchItem,
    Inspection,
    InspectionOrderNumber,
    InspectionRevision,
    InspectionStatus,
    Project,
    Supplier,
    User,
)


class InspectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_orders(
        self, normalized_orders: list[str], work_type: InspectionWorkType
    ) -> None:
        if self.session.get_bind().dialect.name != "postgresql":
            return
        for order_number in sorted(set(normalized_orders)):
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": f"{work_type.value}:{order_number}"},
            )

    async def existing_orders(
        self,
        normalized_orders: list[str],
        work_type: InspectionWorkType,
        *,
        exclude_inspection_id: int | None = None,
    ) -> set[str]:
        if not normalized_orders:
            return set()
        statement = (
            select(InspectionOrderNumber.order_number_normalized)
            .join(Inspection, Inspection.id == InspectionOrderNumber.inspection_id)
            .where(
                InspectionOrderNumber.order_number_normalized.in_(normalized_orders),
                Inspection.work_type == work_type,
            )
        )
        if exclude_inspection_id is not None:
            statement = statement.where(Inspection.id != exclude_inspection_id)
        return set((await self.session.scalars(statement)).all())

    async def order_exists(
        self,
        normalized_order: str,
        work_type: InspectionWorkType,
        *,
        exclude_inspection_id: int | None = None,
    ) -> bool:
        return bool(
            await self.existing_orders(
                [normalized_order],
                work_type,
                exclude_inspection_id=exclude_inspection_id,
            )
        )

    async def get_references(
        self,
        *,
        author_id: int,
        supplier_id: int | None,
        status_id: int,
        project_id: int,
    ) -> tuple[User | None, Supplier | None, InspectionStatus | None, Project | None]:
        return (
            await self.session.get(User, author_id),
            await self.session.get(Supplier, supplier_id) if supplier_id is not None else None,
            await self.session.get(InspectionStatus, status_id),
            await self.session.get(Project, project_id),
        )

    async def get_user(self, user_id: int) -> User | None:
        return await self.session.get(User, user_id)

    async def first_project_for_order(
        self, normalized_order: str, work_type: InspectionWorkType
    ) -> Project | None:
        return await self.session.scalar(
            select(Project)
            .join(Inspection, Inspection.project_id == Project.id)
            .join(
                InspectionOrderNumber,
                InspectionOrderNumber.inspection_id == Inspection.id,
            )
            .where(
                InspectionOrderNumber.order_number_normalized == normalized_order,
                Inspection.work_type == work_type,
            )
            .order_by(Inspection.id)
            .limit(1)
        )

    async def get_with_details(self, inspection_id: int) -> Inspection | None:
        return await self.session.scalar(
            select(Inspection)
            .options(
                selectinload(Inspection.author),
                selectinload(Inspection.supplier),
                selectinload(Inspection.status),
                selectinload(Inspection.project),
                selectinload(Inspection.order_numbers),
            )
            .where(Inspection.id == inspection_id)
        )

    async def list_for_user(
        self,
        user_id: int | None,
        start: date,
        end: date,
        *,
        limit: int,
        offset: int,
    ) -> list[Inspection]:
        statement = (
            select(Inspection)
            .options(
                selectinload(Inspection.author),
                selectinload(Inspection.status),
                selectinload(Inspection.project),
                selectinload(Inspection.order_numbers),
            )
            .where(Inspection.inspection_date.between(start, end))
        )
        if user_id is not None:
            statement = statement.where(Inspection.author_id == user_id)
        return list(
            (
                await self.session.scalars(
                    statement.order_by(
                        Inspection.inspection_date.desc(), Inspection.id.desc()
                    )
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        )

    async def is_processed(self, inspection_id: int) -> bool:
        return bool(
            await self.session.scalar(
                select(
                    exists(
                        select(ExportBatchItem.inspection_id)
                        .join(ExportBatch, ExportBatch.id == ExportBatchItem.batch_id)
                        .join(
                            Inspection,
                            Inspection.id == ExportBatchItem.inspection_id,
                        )
                        .where(
                            ExportBatchItem.inspection_id == inspection_id,
                            ExportBatch.affects_processing.is_(True),
                            ExportBatch.state == ExportState.SENT,
                            or_(
                                Inspection.updated_at.is_(None),
                                ExportBatch.sent_at >= Inspection.updated_at,
                            ),
                        )
                    )
                )
            )
        )

    def add(self, inspection: Inspection) -> None:
        self.session.add(inspection)

    def add_revision(self, revision: InspectionRevision) -> None:
        self.session.add(revision)

    async def flush(self) -> None:
        await self.session.flush()

    async def count_for_user(self, user_id: int | None, start: date, end: date) -> int:
        statement = select(func.count(Inspection.id)).where(
            Inspection.inspection_date.between(start, end)
        )
        if user_id is not None:
            statement = statement.where(Inspection.author_id == user_id)
        result = await self.session.scalar(statement)
        return int(result or 0)
