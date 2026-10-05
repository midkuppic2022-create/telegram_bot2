from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import ExportGroup, SupplierStatus
from app.errors import DomainError, EntityNotFound
from app.models import InspectionStatus, Project, Supplier
from app.repositories.directories import DirectoryRepository
from app.validators import clean_display_name, normalize_name

DirectoryKind = Literal["status", "project", "supplier"]


class DirectoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.repository = DirectoryRepository(session)

    async def list_statuses(self, *, active_only: bool = True) -> list[InspectionStatus]:
        return await self.repository.list_statuses(active_only=active_only)

    async def get_status(self, status_id: int) -> InspectionStatus | None:
        entity = await self.repository.get(InspectionStatus, status_id)
        return entity if isinstance(entity, InspectionStatus) else None

    async def list_projects(self, *, active_only: bool = True) -> list[Project]:
        return await self.repository.list_projects(active_only=active_only)

    async def get_project(self, project_id: int) -> Project | None:
        entity = await self.repository.get(Project, project_id)
        return entity if isinstance(entity, Project) else None

    async def list_suppliers(
        self,
        *,
        active_only: bool = True,
        status: SupplierStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Supplier]:
        return await self.repository.list_suppliers(
            active_only=active_only,
            status=status,
            limit=limit,
            offset=offset,
        )

    async def get_supplier(
        self, supplier_id: int, *, follow_merge: bool = False
    ) -> Supplier | None:
        supplier = await self.repository.get_supplier(supplier_id)
        if follow_merge and supplier is not None and supplier.merged_into_id is not None:
            return await self.repository.get_supplier(supplier.merged_into_id)
        return supplier

    async def get_entity(self, kind: DirectoryKind, entity_id: int) -> object | None:
        model, _ = self._model_for(kind)
        return await self.repository.get(model, entity_id)

    async def search_suppliers(
        self,
        query: str,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> list[Supplier]:
        normalized = normalize_name(query)
        if not normalized:
            return []
        return await self.repository.search_suppliers(
            normalized,
            limit=limit,
            offset=offset,
        )

    async def resolve_manual_supplier(self, name: str, created_by_id: int) -> Supplier:
        cleaned = clean_display_name(name)
        normalized = normalize_name(cleaned)
        supplier = await self.repository.find_by_normalized_name(Supplier, normalized)
        if supplier is not None:
            if supplier.merged_into_id is not None:
                target = await self.repository.get_supplier(supplier.merged_into_id)
                if target is not None:
                    return target
            if supplier.status == SupplierStatus.INACTIVE:
                supplier.status = SupplierStatus.PENDING
                supplier.created_by_id = created_by_id
            return supplier
        supplier = Supplier(
            name=cleaned,
            normalized_name=normalized,
            status=SupplierStatus.PENDING,
            created_by_id=created_by_id,
        )
        self.repository.add(supplier)
        await self.repository.flush()
        return supplier

    async def add_supplier(self, name: str, *, active: bool, created_by_id: int) -> Supplier:
        supplier = await self.resolve_manual_supplier(name, created_by_id)
        supplier.status = SupplierStatus.ACTIVE if active else SupplierStatus.PENDING
        await self.repository.flush()
        return supplier

    async def add_status(self, name: str, *, allows_duplicate: bool) -> InspectionStatus:
        cleaned = clean_display_name(name, max_length=120)
        normalized = normalize_name(cleaned)
        existing = await self.repository.find_by_normalized_name(InspectionStatus, normalized)
        if existing:
            existing.name = cleaned
            existing.active = True
            existing.allows_duplicate = allows_duplicate
            return existing
        entity = InspectionStatus(
            name=cleaned,
            normalized_name=normalized,
            active=True,
            allows_duplicate=allows_duplicate,
        )
        self.repository.add(entity)
        await self.repository.flush()
        return entity

    async def add_project(self, name: str, *, export_group: ExportGroup) -> Project:
        cleaned = clean_display_name(name, max_length=120)
        normalized = normalize_name(cleaned)
        existing = await self.repository.find_by_normalized_name(Project, normalized)
        if existing:
            existing.name = cleaned
            existing.active = True
            existing.export_group = export_group
            return existing
        entity = Project(
            name=cleaned,
            normalized_name=normalized,
            active=True,
            export_group=export_group,
        )
        self.repository.add(entity)
        await self.repository.flush()
        return entity

    async def rename(self, kind: DirectoryKind, entity_id: int, name: str) -> object:
        model, max_length = self._model_for(kind)
        entity = await self.repository.get(model, entity_id)
        if entity is None:
            raise EntityNotFound("Элемент справочника не найден.")
        cleaned = clean_display_name(name, max_length=max_length)
        normalized = normalize_name(cleaned)
        duplicate = await self.repository.find_renaming_conflict(model, entity_id, normalized)
        if duplicate:
            raise DomainError("Элемент с таким названием уже существует.")
        entity.name = cleaned
        entity.normalized_name = normalized
        await self.repository.flush()
        return entity

    async def set_active(self, kind: DirectoryKind, entity_id: int, active: bool) -> object:
        model, _ = self._model_for(kind)
        entity = await self.repository.get(model, entity_id)
        if entity is None:
            raise EntityNotFound("Элемент справочника не найден.")
        if kind == "supplier":
            entity.status = SupplierStatus.ACTIVE if active else SupplierStatus.INACTIVE
        else:
            entity.active = active
        await self.repository.flush()
        return entity

    async def approve_supplier(self, supplier_id: int) -> Supplier:
        supplier = await self.repository.get_supplier(supplier_id)
        if supplier is None:
            raise EntityNotFound("Поставщик не найден.")
        supplier.status = SupplierStatus.ACTIVE
        supplier.merged_into_id = None
        await self.repository.flush()
        return supplier

    async def reject_supplier(self, supplier_id: int) -> Supplier:
        supplier = await self.repository.get_supplier(supplier_id)
        if supplier is None:
            raise EntityNotFound("Поставщик не найден.")
        if supplier.status != SupplierStatus.PENDING:
            raise DomainError("Поставщик уже обработан.")
        supplier.status = SupplierStatus.INACTIVE
        supplier.merged_into_id = None
        await self.repository.flush()
        return supplier

    async def merge_supplier(self, source_id: int, target_id: int) -> Supplier:
        if source_id == target_id:
            raise DomainError("Нельзя объединить поставщика с самим собой.")
        source = await self.repository.get_supplier(source_id)
        target = await self.repository.get_supplier(target_id)
        if source is None or target is None:
            raise EntityNotFound("Поставщик не найден.")
        if target.status != SupplierStatus.ACTIVE:
            raise DomainError("Объединять можно только с активным поставщиком.")
        await self.repository.move_inspections(source_id, target_id)
        source.status = SupplierStatus.INACTIVE
        source.merged_into_id = target_id
        await self.repository.flush()
        return target

    @staticmethod
    def _model_for(kind: DirectoryKind) -> tuple[type, int]:
        if kind == "status":
            return InspectionStatus, 120
        if kind == "project":
            return Project, 120
        if kind == "supplier":
            return Supplier, 255
        raise ValueError(f"Unknown directory kind: {kind}")
