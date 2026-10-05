from typing import Literal

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import SupplierStatus
from app.models import Inspection, InspectionStatus, Project, Supplier

DirectoryModel = type[InspectionStatus] | type[Project] | type[Supplier]
DirectoryKind = Literal["status", "project", "supplier"]


class DirectoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_statuses(self, *, active_only: bool) -> list[InspectionStatus]:
        statement: Select = select(InspectionStatus).order_by(InspectionStatus.name)
        if active_only:
            statement = statement.where(InspectionStatus.active.is_(True))
        return list((await self.session.scalars(statement)).all())

    async def list_projects(self, *, active_only: bool) -> list[Project]:
        statement: Select = select(Project).order_by(Project.export_group, Project.name)
        if active_only:
            statement = statement.where(Project.active.is_(True))
        return list((await self.session.scalars(statement)).all())

    async def list_suppliers(
        self,
        *,
        active_only: bool,
        status: SupplierStatus | None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Supplier]:
        statement = select(Supplier).order_by(Supplier.name).offset(offset)
        if status is not None:
            statement = statement.where(Supplier.status == status)
        elif active_only:
            statement = statement.where(Supplier.status == SupplierStatus.ACTIVE)
        if limit is not None:
            statement = statement.limit(limit)
        return list((await self.session.scalars(statement)).all())

    async def search_suppliers(
        self, normalized_query: str, *, limit: int, offset: int
    ) -> list[Supplier]:
        statement = (
            select(Supplier)
            .where(
                Supplier.status == SupplierStatus.ACTIVE,
                Supplier.normalized_name.contains(normalized_query),
            )
            .order_by(Supplier.name)
            .offset(offset)
            .limit(limit)
        )
        return list((await self.session.scalars(statement)).all())

    async def get(self, model: DirectoryModel, entity_id: int) -> object | None:
        return await self.session.get(model, entity_id)

    async def get_supplier(self, supplier_id: int) -> Supplier | None:
        return await self.session.get(Supplier, supplier_id)

    async def find_by_normalized_name(
        self, model: DirectoryModel, normalized_name: str
    ) -> object | None:
        return await self.session.scalar(
            select(model).where(model.normalized_name == normalized_name)
        )

    async def find_renaming_conflict(
        self, model: DirectoryModel, entity_id: int, normalized_name: str
    ) -> object | None:
        return await self.session.scalar(
            select(model).where(
                model.normalized_name == normalized_name,
                model.id != entity_id,
            )
        )

    def add(self, entity: object) -> None:
        self.session.add(entity)

    async def move_inspections(self, source_id: int, target_id: int) -> None:
        await self.session.execute(
            update(Inspection)
            .where(Inspection.supplier_id == source_id)
            .values(supplier_id=target_id)
        )

    async def flush(self) -> None:
        await self.session.flush()
