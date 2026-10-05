from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.enums import (
    ExportGroup,
    ExportMode,
    ExportState,
    InspectionScenario,
    InspectionWorkType,
    ProjectCode,
    ReconciliationState,
    SupplierStatus,
    UserRole,
    UserStatus,
)


def enum_column(enum_type: type, name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        validate_strings=True,
        values_callable=lambda enum_class: [item.value for item in enum_class],
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    requested_role: Mapped[UserRole | None] = mapped_column(
        enum_column(UserRole, "requested_user_role")
    )
    role: Mapped[UserRole | None] = mapped_column(enum_column(UserRole, "user_role"))
    status: Mapped[UserStatus] = mapped_column(
        enum_column(UserStatus, "user_status"), default=UserStatus.PENDING, nullable=False
    )

    inspections: Mapped[list[Inspection]] = relationship(back_populates="author")
    reference_uploads: Mapped[list[ReferenceUpload]] = relationship(
        back_populates="uploaded_by"
    )


class Supplier(TimestampMixin, Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    status: Mapped[SupplierStatus] = mapped_column(
        enum_column(SupplierStatus, "supplier_status"),
        default=SupplierStatus.ACTIVE,
        nullable=False,
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    merged_into_id: Mapped[int | None] = mapped_column(
        ForeignKey("suppliers.id", ondelete="SET NULL")
    )

    inspections: Mapped[list[Inspection]] = relationship(back_populates="supplier")
    merged_into: Mapped[Supplier | None] = relationship(remote_side="Supplier.id")


class InspectionStatus(TimestampMixin, Base):
    __tablename__ = "inspection_statuses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    code: Mapped[InspectionScenario | None] = mapped_column(
        enum_column(InspectionScenario, "inspection_status_code"),
        nullable=True,
        unique=True,
    )
    allows_duplicate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    supports_related_orders: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    inspections: Mapped[list[Inspection]] = relationship(back_populates="status")


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    code: Mapped[ProjectCode | None] = mapped_column(
        enum_column(ProjectCode, "project_code"), nullable=True, unique=True
    )
    export_group: Mapped[ExportGroup] = mapped_column(
        enum_column(ExportGroup, "export_group"), nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    inspections: Mapped[list[Inspection]] = relationship(back_populates="project")


class Inspection(Base):
    __tablename__ = "inspections"
    __table_args__ = (
        CheckConstraint("rate >= 0", name="ck_inspections_rate_nonnegative"),
        CheckConstraint("fuel >= 0", name="ck_inspections_fuel_nonnegative"),
        Index("ix_inspections_date_author", "inspection_date", "author_id"),
        Index("ix_inspections_work_type", "work_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    author_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    work_type: Mapped[InspectionWorkType] = mapped_column(
        enum_column(InspectionWorkType, "inspection_work_type"), nullable=False
    )
    inspection_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    order_number_raw: Mapped[str] = mapped_column(String(64), nullable=False)
    order_number_normalized: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    supplier_id: Mapped[int | None] = mapped_column(
        ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=True
    )
    status_id: Mapped[int] = mapped_column(
        ForeignKey("inspection_statuses.id", ondelete="RESTRICT"), nullable=False
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    rate: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    fuel: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text)
    reconciliation_status: Mapped[str | None] = mapped_column(String(80))
    scenario: Mapped[InspectionScenario] = mapped_column(
        enum_column(InspectionScenario, "inspection_scenario"),
        default=InspectionScenario.NEXT,
        nullable=False,
    )
    vehicle_number: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    author: Mapped[User] = relationship(back_populates="inspections")
    supplier: Mapped[Supplier | None] = relationship(back_populates="inspections")
    status: Mapped[InspectionStatus] = relationship(back_populates="inspections")
    project: Mapped[Project] = relationship(back_populates="inspections")
    export_items: Mapped[list[ExportBatchItem]] = relationship(back_populates="inspection")
    order_numbers: Mapped[list[InspectionOrderNumber]] = relationship(
        back_populates="inspection",
        cascade="all, delete-orphan",
        order_by="InspectionOrderNumber.position",
    )
    revisions: Mapped[list[InspectionRevision]] = relationship(
        back_populates="inspection", cascade="all, delete-orphan"
    )


class InspectionOrderNumber(Base):
    __tablename__ = "inspection_order_numbers"
    __table_args__ = (
        CheckConstraint("position >= 0", name="ck_inspection_order_numbers_position"),
        UniqueConstraint(
            "inspection_id", "position", name="uq_inspection_order_numbers_position"
        ),
        UniqueConstraint(
            "inspection_id",
            "order_number_normalized",
            name="uq_inspection_order_numbers_value",
        ),
        Index(
            "ix_inspection_order_numbers_normalized", "order_number_normalized"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inspection_id: Mapped[int] = mapped_column(
        ForeignKey("inspections.id", ondelete="CASCADE"), nullable=False
    )
    order_number_raw: Mapped[str] = mapped_column(String(64), nullable=False)
    order_number_normalized: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    supplier_name_snapshot: Mapped[str | None] = mapped_column(String(255))
    reconciliation_state: Mapped[ReconciliationState] = mapped_column(
        enum_column(ReconciliationState, "reconciliation_state"),
        default=ReconciliationState.NO_FILE,
        nullable=False,
    )
    reference_upload_id: Mapped[int | None] = mapped_column(
        ForeignKey("reference_uploads.id", ondelete="SET NULL"), nullable=True
    )

    inspection: Mapped[Inspection] = relationship(back_populates="order_numbers")
    reference_upload: Mapped[ReferenceUpload | None] = relationship()


class ReferenceUpload(Base):
    __tablename__ = "reference_uploads"
    __table_args__ = (
        Index("ix_reference_uploads_active", "active"),
        Index(
            "uq_reference_uploads_single_active",
            "active",
            unique=True,
            postgresql_where=text("active"),
            sqlite_where=text("active"),
        ),
        CheckConstraint("row_count >= 0", name="ck_reference_uploads_row_count"),
        CheckConstraint("warning_count >= 0", name="ck_reference_uploads_warning_count"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uploaded_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    uploaded_by: Mapped[User] = relationship(back_populates="reference_uploads")
    rows: Mapped[list[ReferenceOrder]] = relationship(
        back_populates="upload", cascade="all, delete-orphan"
    )


class ReferenceOrder(Base):
    __tablename__ = "reference_orders"
    __table_args__ = (
        UniqueConstraint(
            "upload_id", "order_number_normalized", name="uq_reference_orders_upload_order"
        ),
        Index("ix_reference_orders_lookup", "upload_id", "order_number_normalized"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    upload_id: Mapped[int] = mapped_column(
        ForeignKey("reference_uploads.id", ondelete="CASCADE"), nullable=False
    )
    order_number_raw: Mapped[str] = mapped_column(String(64), nullable=False)
    order_number_normalized: Mapped[str] = mapped_column(String(64), nullable=False)
    supplier_name: Mapped[str | None] = mapped_column(String(255))

    upload: Mapped[ReferenceUpload] = relationship(back_populates="rows")


class InspectionRevision(Base):
    __tablename__ = "inspection_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inspection_id: Mapped[int] = mapped_column(
        ForeignKey("inspections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    changed_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    before_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    inspection: Mapped[Inspection] = relationship(back_populates="revisions")
    changed_by: Mapped[User] = relationship()


class ExportBatch(Base):
    __tablename__ = "export_batches"
    __table_args__ = (CheckConstraint("period_from <= period_to", name="ck_export_batches_period"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requested_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    period_from: Mapped[date] = mapped_column(Date, nullable=False)
    period_to: Mapped[date] = mapped_column(Date, nullable=False)
    expert_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    mode: Mapped[ExportMode] = mapped_column(enum_column(ExportMode, "export_mode"), nullable=False)
    state: Mapped[ExportState] = mapped_column(
        enum_column(ExportState, "export_state"),
        default=ExportState.PREPARING,
        nullable=False,
        index=True,
    )
    affects_processing: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(500))
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    requested_by: Mapped[User] = relationship(foreign_keys=[requested_by_id])
    expert: Mapped[User | None] = relationship(foreign_keys=[expert_id])
    items: Mapped[list[ExportBatchItem]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class ExportBatchItem(Base):
    __tablename__ = "export_batch_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "inspection_id", name="uq_export_batch_items_pair"),
    )

    batch_id: Mapped[int] = mapped_column(
        ForeignKey("export_batches.id", ondelete="CASCADE"), primary_key=True
    )
    inspection_id: Mapped[int] = mapped_column(
        ForeignKey("inspections.id", ondelete="RESTRICT"), primary_key=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)

    batch: Mapped[ExportBatch] = relationship(back_populates="items")
    inspection: Mapped[Inspection] = relationship(back_populates="export_items")
