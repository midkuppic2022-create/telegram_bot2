"""Track registration notifications sent to administrators.

Revision ID: 0006_registration_notifications
Revises: 0005_inspection_soft_delete
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_registration_notifications"
down_revision: str | None = "0005_inspection_soft_delete"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "registration_admin_notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("admin_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "admin_chat_id",
            "telegram_message_id",
            name="uq_registration_admin_notifications_message",
        ),
    )
    op.create_index(
        "ix_registration_admin_notifications_user_id",
        "registration_admin_notifications",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_registration_admin_notifications_user_id",
        table_name="registration_admin_notifications",
    )
    op.drop_table("registration_admin_notifications")
