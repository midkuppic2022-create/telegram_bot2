"""Add inspection work types, related orders and edit history.

Revision ID: 0003_work_types_orders_edits
Revises: 0002_specialist_fuel_optional
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_work_types_orders_edits"
down_revision: str | None = "0002_specialist_fuel_optional"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "inspection_statuses",
        sa.Column(
            "supports_related_orders",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(
        sa.text(
            "UPDATE inspection_statuses "
            "SET supports_related_orders = TRUE "
            "WHERE normalized_name = 'отгружен с другим заказом'"
        )
    )

    op.add_column(
        "inspections",
        sa.Column(
            "work_type",
            sa.Enum(
                "expert",
                "specialist",
                name="inspection_work_type",
                native_enum=False,
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "inspections", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        sa.text(
            "UPDATE inspections AS i "
            "SET work_type = CASE "
            "WHEN u.role = 'specialist' THEN 'specialist' ELSE 'expert' END "
            "FROM users AS u WHERE u.id = i.author_id"
        )
    )
    op.alter_column("inspections", "work_type", nullable=False)
    op.create_index("ix_inspections_work_type", "inspections", ["work_type"])

    op.create_table(
        "inspection_order_numbers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "inspection_id",
            sa.Integer(),
            sa.ForeignKey("inspections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("order_number_raw", sa.String(64), nullable=False),
        sa.Column("order_number_normalized", sa.String(64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "position >= 0", name="ck_inspection_order_numbers_position"
        ),
        sa.UniqueConstraint(
            "inspection_id", "position", name="uq_inspection_order_numbers_position"
        ),
        sa.UniqueConstraint(
            "inspection_id",
            "order_number_normalized",
            name="uq_inspection_order_numbers_value",
        ),
    )
    op.create_index(
        "ix_inspection_order_numbers_normalized",
        "inspection_order_numbers",
        ["order_number_normalized"],
    )
    op.execute(
        sa.text(
            "INSERT INTO inspection_order_numbers "
            "(inspection_id, order_number_raw, order_number_normalized, position) "
            "SELECT id, order_number_raw, order_number_normalized, 0 FROM inspections"
        )
    )

    op.create_table(
        "inspection_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "inspection_id",
            sa.Integer(),
            sa.ForeignKey("inspections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "changed_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("before_data", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_inspection_revisions_inspection_id",
        "inspection_revisions",
        ["inspection_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_inspection_revisions_inspection_id", table_name="inspection_revisions"
    )
    op.drop_table("inspection_revisions")
    op.drop_index(
        "ix_inspection_order_numbers_normalized",
        table_name="inspection_order_numbers",
    )
    op.drop_table("inspection_order_numbers")
    op.drop_index("ix_inspections_work_type", table_name="inspections")
    op.drop_column("inspections", "updated_at")
    op.drop_column("inspections", "work_type")
    op.drop_column("inspection_statuses", "supports_related_orders")
