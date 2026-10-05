"""Initial inspection accounting schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(64)),
        sa.Column("full_name", sa.String(160), nullable=False),
        sa.Column(
            "requested_role",
            sa.Enum("expert", "specialist", "admin", name="requested_user_role", native_enum=False),
        ),
        sa.Column(
            "role", sa.Enum("expert", "specialist", "admin", name="user_role", native_enum=False)
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "active", "rejected", "blocked", name="user_status", native_enum=False
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("telegram_id", name="uq_users_telegram_id"),
    )
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"])

    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "active", "inactive", name="supplier_status", native_enum=False),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "merged_into_id", sa.Integer(), sa.ForeignKey("suppliers.id", ondelete="SET NULL")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("normalized_name", name="uq_suppliers_normalized_name"),
    )

    op.create_table(
        "inspection_statuses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("normalized_name", sa.String(120), nullable=False),
        sa.Column("allows_duplicate", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("normalized_name", name="uq_inspection_statuses_normalized_name"),
    )

    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("normalized_name", sa.String(120), nullable=False),
        sa.Column(
            "export_group",
            sa.Enum("АК", "СВ", "Х5", name="export_group", native_enum=False),
            nullable=False,
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("normalized_name", name="uq_projects_normalized_name"),
    )

    op.create_table(
        "inspections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "author_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("inspection_date", sa.Date(), nullable=False),
        sa.Column("order_number_raw", sa.String(64), nullable=False),
        sa.Column("order_number_normalized", sa.String(64), nullable=False),
        sa.Column(
            "supplier_id",
            sa.Integer(),
            sa.ForeignKey("suppliers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "status_id",
            sa.Integer(),
            sa.ForeignKey("inspection_statuses.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("rate", sa.Numeric(12, 2), nullable=False),
        sa.Column("fuel", sa.Numeric(12, 2), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column("reconciliation_status", sa.String(80)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("rate >= 0", name="ck_inspections_rate_nonnegative"),
        sa.CheckConstraint("fuel >= 0", name="ck_inspections_fuel_nonnegative"),
    )
    op.create_index("ix_inspections_inspection_date", "inspections", ["inspection_date"])
    op.create_index(
        "ix_inspections_order_number_normalized", "inspections", ["order_number_normalized"]
    )
    op.create_index("ix_inspections_date_author", "inspections", ["inspection_date", "author_id"])

    op.create_table(
        "export_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "requested_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("period_from", sa.Date(), nullable=False),
        sa.Column("period_to", sa.Date(), nullable=False),
        sa.Column("expert_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "mode", sa.Enum("new", "full", name="export_mode", native_enum=False), nullable=False
        ),
        sa.Column(
            "state",
            sa.Enum("preparing", "sent", "failed", name="export_state", native_enum=False),
            nullable=False,
        ),
        sa.Column("affects_processing", sa.Boolean(), nullable=False),
        sa.Column("error_message", sa.String(500)),
        sa.Column("telegram_chat_id", sa.BigInteger()),
        sa.Column("telegram_message_id", sa.BigInteger()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "attempted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("period_from <= period_to", name="ck_export_batches_period"),
    )
    op.create_index("ix_export_batches_state", "export_batches", ["state"])

    op.create_table(
        "export_batch_items",
        sa.Column(
            "batch_id",
            sa.Integer(),
            sa.ForeignKey("export_batches.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "inspection_id",
            sa.Integer(),
            sa.ForeignKey("inspections.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.UniqueConstraint("batch_id", "inspection_id", name="uq_export_batch_items_pair"),
    )


def downgrade() -> None:
    op.drop_table("export_batch_items")
    op.drop_index("ix_export_batches_state", table_name="export_batches")
    op.drop_table("export_batches")
    op.drop_index("ix_inspections_date_author", table_name="inspections")
    op.drop_index("ix_inspections_order_number_normalized", table_name="inspections")
    op.drop_index("ix_inspections_inspection_date", table_name="inspections")
    op.drop_table("inspections")
    op.drop_table("projects")
    op.drop_table("inspection_statuses")
    op.drop_table("suppliers")
    op.drop_index("ix_users_telegram_id", table_name="users")
    op.drop_table("users")
