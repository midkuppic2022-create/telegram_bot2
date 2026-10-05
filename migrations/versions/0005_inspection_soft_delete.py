"""Add recoverable deletion for inspections.

Revision ID: 0005_inspection_soft_delete
Revises: 0004_chat_bot_v2
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_inspection_soft_delete"
down_revision: str | None = "0004_chat_bot_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "inspections",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "inspections",
        sa.Column("deleted_by_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_inspections_deleted_by_id_users",
        "inspections",
        "users",
        ["deleted_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_inspections_deleted_at",
        "inspections",
        ["deleted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_inspections_deleted_at", table_name="inspections")
    op.drop_constraint(
        "fk_inspections_deleted_by_id_users",
        "inspections",
        type_="foreignkey",
    )
    op.drop_column("inspections", "deleted_by_id")
    op.drop_column("inspections", "deleted_at")
