"""Allow inspections without fuel for specialists.

Revision ID: 0002_specialist_fuel_optional
Revises: 0001_initial
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_specialist_fuel_optional"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "inspections",
        "fuel",
        existing_type=sa.Numeric(12, 2),
        nullable=True,
    )


def downgrade() -> None:
    op.execute(sa.text("UPDATE inspections SET fuel = 0 WHERE fuel IS NULL"))
    op.alter_column(
        "inspections",
        "fuel",
        existing_type=sa.Numeric(12, 2),
        nullable=False,
    )
