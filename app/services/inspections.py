from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import (
    InspectionScenario,
    InspectionWorkType,
    ProjectCode,
    ReconciliationState,
    SupplierStatus,
    UserRole,
    UserStatus,
)
from app.errors import DomainError, DuplicateOrderNotAllowed, EntityNotFound
from app.models import Inspection, InspectionOrderNumber, InspectionRevision, InspectionStatus, User
from app.repositories.inspections import InspectionRepository

MAX_ORDERS_PER_INSPECTION = 10


@dataclass(slots=True, frozen=True)
class OrderNumberInput:
    raw: str
    normalized: str
    supplier_name: str | None = None
    reconciliation_state: ReconciliationState = ReconciliationState.NO_FILE
    reference_upload_id: int | None = None


@dataclass(slots=True)
class InspectionInput:
    author_id: int
    work_type: InspectionWorkType
    inspection_date: date
    order_number_raw: str
    order_number_normalized: str
    supplier_id: int | None
    status_id: int
    project_id: int
    rate: Decimal
    comment: str | None
    related_orders: tuple[OrderNumberInput, ...] = ()
    primary_supplier_name: str | None = None
    primary_reconciliation_state: ReconciliationState = ReconciliationState.NO_FILE
    primary_reference_upload_id: int | None = None
    scenario: InspectionScenario | None = None
    vehicle_number: str | None = None


@dataclass(slots=True)
class InspectionUpdateInput:
    inspection_id: int
    changed_by_id: int
    inspection_date: date
    order_number_raw: str
    order_number_normalized: str
    supplier_id: int | None
    status_id: int
    project_id: int
    rate: Decimal
    comment: str | None
    related_orders: tuple[OrderNumberInput, ...] = ()
    primary_supplier_name: str | None = None
    primary_reconciliation_state: ReconciliationState = ReconciliationState.NO_FILE
    primary_reference_upload_id: int | None = None
    scenario: InspectionScenario | None = None
    vehicle_number: str | None = None


class InspectionService:
    def __init__(self, session: AsyncSession) -> None:
        self.repository = InspectionRepository(session)

    async def order_exists(
        self,
        normalized_order: str,
        work_type: InspectionWorkType,
        *,
        exclude_inspection_id: int | None = None,
    ) -> bool:
        return await self.repository.order_exists(
            normalized_order,
            work_type,
            exclude_inspection_id=exclude_inspection_id,
        )

    async def first_project_for_order(self, normalized_order: str, work_type: InspectionWorkType):
        return await self.repository.first_project_for_order(normalized_order, work_type)

    @staticmethod
    def work_type_for_user(user: User) -> InspectionWorkType:
        if user.role is UserRole.SPECIALIST:
            return InspectionWorkType.SPECIALIST
        return InspectionWorkType.EXPERT

    @staticmethod
    def _validate_author_for_work_type(author: User, work_type: InspectionWorkType) -> None:
        if author.status is not UserStatus.ACTIVE or author.role not in {
            UserRole.EXPERT,
            UserRole.SPECIALIST,
            UserRole.ADMIN,
        }:
            raise DomainError("У пользователя нет права добавлять инспекции.")
        if author.role is UserRole.ADMIN:
            return
        expected = (
            InspectionWorkType.SPECIALIST
            if author.role is UserRole.SPECIALIST
            else InspectionWorkType.EXPERT
        )
        if work_type is not expected:
            raise DomainError("Вид работы не соответствует вашей роли.")

    @staticmethod
    def _validate_active_author(author: User) -> None:
        if author.status is not UserStatus.ACTIVE or author.role not in {
            UserRole.EXPERT,
            UserRole.SPECIALIST,
            UserRole.ADMIN,
        }:
            raise DomainError("У пользователя нет права изменять инспекции.")

    @staticmethod
    def _snapshot(inspection: Inspection, *, change_type: str) -> dict:
        return {
            "change_type": change_type,
            "inspection_date": inspection.inspection_date.isoformat(),
            "orders": [
                {
                    "raw": item.order_number_raw,
                    "normalized": item.order_number_normalized,
                    "supplier_name": item.supplier_name_snapshot,
                    "reconciliation_state": item.reconciliation_state.value,
                    "reference_upload_id": item.reference_upload_id,
                }
                for item in inspection.order_numbers
            ],
            "supplier_id": inspection.supplier_id,
            "status_id": inspection.status_id,
            "status_name": inspection.status.name,
            "status_code": (inspection.status.code.value if inspection.status.code else None),
            "project_id": inspection.project_id,
            "project_name": inspection.project.name,
            "project_code": (inspection.project.code.value if inspection.project.code else None),
            "rate": str(inspection.rate),
            "comment": inspection.comment,
            "scenario": inspection.scenario.value,
            "vehicle_number": inspection.vehicle_number,
        }

    @staticmethod
    def _prepare_orders(
        primary_raw: str,
        primary_normalized: str,
        related_orders: tuple[OrderNumberInput, ...],
        *,
        primary_supplier_name: str | None,
        primary_reconciliation_state: ReconciliationState,
        primary_reference_upload_id: int | None,
    ) -> list[OrderNumberInput]:
        orders = [
            OrderNumberInput(
                primary_raw,
                primary_normalized,
                primary_supplier_name,
                primary_reconciliation_state,
                primary_reference_upload_id,
            ),
            *related_orders,
        ]
        if len(orders) > MAX_ORDERS_PER_INSPECTION:
            raise DomainError(
                f"В одной инспекции можно указать не более {MAX_ORDERS_PER_INSPECTION} заказов."
            )
        seen: set[str] = set()
        for order in orders:
            if order.normalized in seen:
                raise DomainError(f"Номер {order.raw} уже добавлен в эту инспекцию.")
            seen.add(order.normalized)
        return orders

    @staticmethod
    def _validate_related_orders(
        scenario: InspectionScenario, orders: list[OrderNumberInput]
    ) -> None:
        if scenario is InspectionScenario.SAME_VEHICLE and len(orders) < 2:
            raise DomainError("Для одной машины добавьте хотя бы один дополнительный заказ.")
        if len(orders) > 1 and scenario not in {
            InspectionScenario.SAME_VEHICLE,
            InspectionScenario.SHIFT,
        }:
            raise DomainError("Дополнительные заказы доступны только для одной машины или смены.")

    async def _validate_references(
        self,
        *,
        author_id: int,
        supplier_id: int | None,
        status_id: int,
        project_id: int,
        allowed_inactive_status_id: int | None = None,
        allowed_inactive_supplier_id: int | None = None,
        allowed_inactive_project_id: int | None = None,
    ):
        author, supplier, status, project = await self.repository.get_references(
            author_id=author_id,
            supplier_id=supplier_id,
            status_id=status_id,
            project_id=project_id,
        )
        if author is None or status is None or project is None:
            raise EntityNotFound("Не удалось найти выбранные значения справочников.")
        if (
            supplier is not None
            and supplier.status is SupplierStatus.INACTIVE
            and supplier.id != allowed_inactive_supplier_id
        ):
            raise DomainError("Поставщик деактивирован.")
        if not status.active and status.id != allowed_inactive_status_id:
            raise DomainError("Статус деактивирован.")
        if not project.active and project.id != allowed_inactive_project_id:
            raise DomainError("Проект деактивирован.")
        return author, supplier, status, project

    @staticmethod
    def _resolve_scenario(
        requested: InspectionScenario | None, status: InspectionStatus
    ) -> InspectionScenario:
        scenario = requested or status.code
        if scenario is None:
            return InspectionScenario.NEXT
        if status.code is not None and status.code is not scenario:
            raise DomainError("Выбранный сценарий не соответствует статусу.")
        return scenario

    @staticmethod
    def _validate_vehicle(
        scenario: InspectionScenario,
        project_code: ProjectCode | None,
        vehicle_number: str | None,
    ) -> str | None:
        cleaned = " ".join(vehicle_number.split()) if vehicle_number else None
        required = (
            scenario is InspectionScenario.SAME_VEHICLE
            and project_code is ProjectCode.X5_SELF_PICKUP
        )
        if required and not cleaned:
            raise DomainError("Для Х5 САМОВЫВОЗ укажите номер автомобиля.")
        if not required:
            # Номер автомобиля — часть только сценария «одна машина»
            # для «Х5 САМОВЫВОЗ». Не переносим его в другой проект при
            # редактировании ранее сохранённой инспекции.
            return None
        if cleaned and len(cleaned) > 64:
            raise DomainError("Номер автомобиля не должен превышать 64 символа.")
        return cleaned

    @staticmethod
    def _validate_scenario_for_flow(
        work_type: InspectionWorkType,
        project_code: ProjectCode | None,
        scenario: InspectionScenario,
    ) -> None:
        if scenario in {InspectionScenario.COMMISSION, InspectionScenario.REPEAT}:
            return
        if work_type is InspectionWorkType.SPECIALIST:
            allowed = {InspectionScenario.NEXT, InspectionScenario.SAME_VEHICLE}
        elif project_code is ProjectCode.X5_SELF_PICKUP:
            allowed = {
                InspectionScenario.CONTROL_SHIPMENT,
                InspectionScenario.SHIFT,
                InspectionScenario.SAME_VEHICLE,
                InspectionScenario.IDLE_TRIP,
            }
        elif project_code is ProjectCode.THUNDER_WATERMELONS:
            allowed = {InspectionScenario.SHIFT, InspectionScenario.NEXT}
        else:
            allowed = {
                InspectionScenario.INSPECTION_STOP,
                # Kept at the service layer so historical records with this
                # scenario remain editable. The V2 UI no longer offers the
                # duplicate button and maps «далее» to this Excel status.
                InspectionScenario.CONTROL_SHIPMENT,
                InspectionScenario.SAME_VEHICLE,
                InspectionScenario.IDLE_TRIP,
                InspectionScenario.NEXT,
            }
        if scenario not in allowed:
            raise DomainError("Статус недоступен для выбранной роли или проекта.")

    async def _check_duplicates(
        self,
        orders: list[OrderNumberInput],
        work_type: InspectionWorkType,
        status: InspectionStatus,
        *,
        exclude_inspection_id: int | None = None,
        allowed_existing_duplicates: set[str] | None = None,
    ) -> None:
        normalized = [order.normalized for order in orders]
        await self.repository.lock_orders(normalized, work_type)
        duplicates = await self.repository.existing_orders(
            normalized,
            work_type,
            exclude_inspection_id=exclude_inspection_id,
        )
        violations = duplicates - (allowed_existing_duplicates or set())
        if violations and not status.allows_duplicate:
            display = ", ".join(order.raw for order in orders if order.normalized in violations)
            raise DuplicateOrderNotAllowed(
                f"Номер уже есть в этом виде работы: {display}. "
                "Повтор можно сохранить только как повторную или комиссионную инспекцию."
            )

    async def create(self, data: InspectionInput) -> Inspection:
        author, supplier, status, project = await self._validate_references(
            author_id=data.author_id,
            supplier_id=data.supplier_id,
            status_id=data.status_id,
            project_id=data.project_id,
        )
        self._validate_author_for_work_type(author, data.work_type)
        orders = self._prepare_orders(
            data.order_number_raw,
            data.order_number_normalized,
            data.related_orders,
            primary_supplier_name=data.primary_supplier_name,
            primary_reconciliation_state=data.primary_reconciliation_state,
            primary_reference_upload_id=data.primary_reference_upload_id,
        )
        scenario = self._resolve_scenario(data.scenario, status)
        self._validate_scenario_for_flow(data.work_type, project.code, scenario)
        self._validate_related_orders(scenario, orders)
        vehicle_number = self._validate_vehicle(scenario, project.code, data.vehicle_number)
        await self._check_duplicates(orders, data.work_type, status)

        if supplier is not None:
            orders = [
                OrderNumberInput(
                    order.raw,
                    order.normalized,
                    order.supplier_name or supplier.name,
                    (
                        order.reconciliation_state
                        if order.supplier_name is not None
                        else ReconciliationState.LEGACY
                    ),
                    order.reference_upload_id,
                )
                for order in orders
            ]

        inspection = Inspection(
            author_id=data.author_id,
            work_type=data.work_type,
            inspection_date=data.inspection_date,
            order_number_raw=data.order_number_raw,
            order_number_normalized=data.order_number_normalized,
            supplier_id=data.supplier_id,
            status_id=data.status_id,
            project_id=data.project_id,
            rate=data.rate,
            # Kept only as a nullable legacy database column.
            fuel=None,
            comment=data.comment,
            scenario=scenario,
            vehicle_number=vehicle_number,
        )
        inspection.order_numbers = [
            InspectionOrderNumber(
                order_number_raw=order.raw,
                order_number_normalized=order.normalized,
                position=position,
                supplier_name_snapshot=order.supplier_name,
                reconciliation_state=order.reconciliation_state,
                reference_upload_id=order.reference_upload_id,
            )
            for position, order in enumerate(orders)
        ]
        self.repository.add(inspection)
        await self.repository.flush()
        return inspection

    async def get_for_user(self, inspection_id: int, user_id: int) -> Inspection:
        inspection = await self.repository.get_with_details(inspection_id)
        editor = await self.repository.get_user(user_id)
        if (
            inspection is None
            or editor is None
            or (inspection.author_id != user_id and editor.role is not UserRole.ADMIN)
        ):
            raise EntityNotFound("Инспекция не найдена.")
        return inspection

    async def update(self, data: InspectionUpdateInput) -> Inspection:
        inspection = await self.get_for_user(data.inspection_id, data.changed_by_id)
        existing_order_numbers = {item.order_number_normalized for item in inspection.order_numbers}
        author, supplier, status, project = await self._validate_references(
            author_id=data.changed_by_id,
            supplier_id=data.supplier_id,
            status_id=data.status_id,
            project_id=data.project_id,
            allowed_inactive_status_id=inspection.status_id,
            allowed_inactive_supplier_id=inspection.supplier_id,
            allowed_inactive_project_id=inspection.project_id,
        )
        self._validate_active_author(author)
        orders = self._prepare_orders(
            data.order_number_raw,
            data.order_number_normalized,
            data.related_orders,
            primary_supplier_name=data.primary_supplier_name,
            primary_reconciliation_state=data.primary_reconciliation_state,
            primary_reference_upload_id=data.primary_reference_upload_id,
        )
        scenario = self._resolve_scenario(data.scenario, status)
        self._validate_scenario_for_flow(inspection.work_type, project.code, scenario)
        self._validate_related_orders(scenario, orders)
        vehicle_number = self._validate_vehicle(scenario, project.code, data.vehicle_number)
        await self._check_duplicates(
            orders,
            inspection.work_type,
            status,
            exclude_inspection_id=inspection.id,
            allowed_existing_duplicates=(
                existing_order_numbers if not inspection.status.allows_duplicate else None
            ),
        )

        self.repository.add_revision(
            InspectionRevision(
                inspection_id=inspection.id,
                changed_by_id=data.changed_by_id,
                before_data=self._snapshot(inspection, change_type="update"),
            )
        )
        inspection.inspection_date = data.inspection_date
        inspection.order_number_raw = data.order_number_raw
        inspection.order_number_normalized = data.order_number_normalized
        inspection.supplier_id = data.supplier_id
        inspection.status_id = data.status_id
        inspection.project_id = data.project_id
        inspection.rate = data.rate
        inspection.fuel = None
        inspection.comment = data.comment
        inspection.scenario = scenario
        inspection.vehicle_number = vehicle_number
        inspection.updated_at = datetime.now(UTC)
        inspection.order_numbers.clear()
        await self.repository.flush()
        inspection.order_numbers = [
            InspectionOrderNumber(
                order_number_raw=order.raw,
                order_number_normalized=order.normalized,
                position=position,
                supplier_name_snapshot=order.supplier_name
                or (supplier.name if supplier is not None else None),
                reconciliation_state=(
                    order.reconciliation_state
                    if order.supplier_name is not None or supplier is None
                    else ReconciliationState.LEGACY
                ),
                reference_upload_id=order.reference_upload_id,
            )
            for position, order in enumerate(orders)
        ]
        await self.repository.flush()
        return inspection

    async def delete(self, inspection_id: int, changed_by_id: int) -> Inspection:
        inspection = await self.get_for_user(inspection_id, changed_by_id)
        editor = await self.repository.get_user(changed_by_id)
        if editor is None:
            raise EntityNotFound("Пользователь не найден.")
        self._validate_active_author(editor)
        self.repository.add_revision(
            InspectionRevision(
                inspection_id=inspection.id,
                changed_by_id=changed_by_id,
                before_data=self._snapshot(inspection, change_type="delete"),
            )
        )
        inspection.deleted_at = datetime.now(UTC)
        inspection.deleted_by_id = changed_by_id
        inspection.updated_at = inspection.deleted_at
        await self.repository.flush()
        return inspection

    async def list_for_user(
        self,
        user_id: int,
        start: date,
        end: date,
        *,
        limit: int,
        offset: int,
    ) -> list[Inspection]:
        user = await self.repository.get_user(user_id)
        author_id = None if user is not None and user.role is UserRole.ADMIN else user_id
        return await self.repository.list_for_user(
            author_id, start, end, limit=limit, offset=offset
        )

    async def is_processed(self, inspection_id: int) -> bool:
        return await self.repository.is_processed(inspection_id)

    async def count_for_user(self, user_id: int, start: date, end: date) -> int:
        user = await self.repository.get_user(user_id)
        author_id = None if user is not None and user.role is UserRole.ADMIN else user_id
        return await self.repository.count_for_user(author_id, start, end)
