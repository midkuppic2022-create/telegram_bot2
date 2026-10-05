"""Add reconciliation snapshots and the version 2 inspection workflow.

Revision ID: 0004_chat_bot_v2
Revises: 0003_work_types_orders_edits
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_chat_bot_v2"
down_revision: str | None = "0003_work_types_orders_edits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SCENARIOS = (
    "next",
    "same_vehicle",
    "shift",
    "control_shipment",
    "inspection_stop",
    "idle_trip",
    "commission",
    "repeat",
)
PROJECT_CODES = (
    "thunder_agro_maf",
    "thunder_watermelons",
    "thunder_self_pickup",
    "x5_self_pickup",
    "x5_techmp_rvi",
)
RECONCILIATION_STATES = ("found", "missing", "no_file", "legacy")


def _rename_project(
    old_name: str,
    new_name: str,
    normalized_name: str,
    code: str,
) -> None:
    op.execute(
        sa.text(
            "UPDATE projects SET name = :new_name, normalized_name = :normalized_name, "
            "code = :code, active = TRUE "
            "WHERE normalized_name = :old_normalized OR code = :code"
        ).bindparams(
            new_name=new_name,
            normalized_name=normalized_name,
            code=code,
            old_normalized=old_name.casefold(),
        )
    )


def _merge_watermelon_projects() -> None:
    connection = op.get_bind()
    rows = list(
        connection.execute(
            sa.text(
                "SELECT id, normalized_name FROM projects "
                "WHERE normalized_name IN ('ак, арбуз', 'св, арбуз', 'тандер, арбузы') "
                "ORDER BY id"
            )
        ).mappings()
    )
    if not rows:
        return
    canonical = next((row for row in rows if row["normalized_name"] == "тандер, арбузы"), rows[0])
    canonical_id = canonical["id"]
    for row in rows:
        if row["id"] == canonical_id:
            continue
        connection.execute(
            sa.text("UPDATE inspections SET project_id = :target WHERE project_id = :source"),
            {"target": canonical_id, "source": row["id"]},
        )
        connection.execute(
            sa.text(
                "UPDATE projects SET name = :name, normalized_name = :normalized, "
                "active = FALSE, code = NULL WHERE id = :project_id"
            ),
            {
                "project_id": row["id"],
                "name": f"{row['normalized_name']} (архив)",
                "normalized": f"{row['normalized_name']} (архив {row['id']})",
            },
        )
    connection.execute(
        sa.text(
            "UPDATE projects SET name = 'Тандер, арбузы', "
            "normalized_name = 'тандер, арбузы', code = 'thunder_watermelons', "
            "export_group = 'АК', active = TRUE WHERE id = :project_id"
        ),
        {"project_id": canonical_id},
    )


def _upsert_statuses() -> None:
    connection = op.get_bind()
    statuses = (
        ("далее", "далее", "next", False, False, None),
        (
            "отгружались в одной авто",
            "отгружались в одной авто",
            "same_vehicle",
            False,
            True,
            "отгружен с другим заказом",
        ),
        ("добавить смену", "добавить смену", "shift", False, True, None),
        (
            "контроль отгрузки",
            "контроль отгрузки",
            "control_shipment",
            False,
            False,
            None,
        ),
        (
            "осмотр / стоп-отгрузка",
            "осмотр / стоп-отгрузка",
            "inspection_stop",
            False,
            False,
            None,
        ),
        ("холостой выезд", "холостой выезд", "idle_trip", False, False, None),
        (
            "комиссионная инспекция",
            "комиссионная инспекция",
            "commission",
            True,
            False,
            None,
        ),
        (
            "повторная инспекция",
            "повторная инспекция",
            "repeat",
            True,
            False,
            None,
        ),
    )
    used_ids: list[int] = []
    for name, normalized, code, allows_duplicate, related, legacy_normalized in statuses:
        legacy_clause = " OR normalized_name = :legacy" if legacy_normalized is not None else ""
        match = connection.execute(
            sa.text(
                "SELECT id FROM inspection_statuses "
                "WHERE code = :code OR normalized_name = :normalized "
                f"{legacy_clause} "
                "ORDER BY id LIMIT 1"
            ),
            {"code": code, "normalized": normalized, "legacy": legacy_normalized},
        ).scalar_one_or_none()
        if match is None:
            match = connection.execute(
                sa.text(
                    "INSERT INTO inspection_statuses "
                    "(name, normalized_name, code, allows_duplicate, supports_related_orders, "
                    "active, created_at, updated_at) VALUES "
                    "(:name, :normalized, :code, :allows_duplicate, :related, TRUE, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) RETURNING id"
                ),
                {
                    "name": name,
                    "normalized": normalized,
                    "code": code,
                    "allows_duplicate": allows_duplicate,
                    "related": related,
                },
            ).scalar_one()
        else:
            connection.execute(
                sa.text(
                    "UPDATE inspection_statuses SET name = :name, normalized_name = :normalized, "
                    "code = :code, allows_duplicate = :allows_duplicate, "
                    "supports_related_orders = :related, active = TRUE WHERE id = :status_id"
                ),
                {
                    "status_id": match,
                    "name": name,
                    "normalized": normalized,
                    "code": code,
                    "allows_duplicate": allows_duplicate,
                    "related": related,
                },
            )
        used_ids.append(match)
    connection.execute(
        sa.text(
            "UPDATE inspection_statuses SET active = FALSE "
            "WHERE code IS NULL AND id NOT IN (" + ",".join(str(item) for item in used_ids) + ")"
        )
    )


def upgrade() -> None:
    op.add_column(
        "inspection_statuses",
        sa.Column(
            "code",
            sa.Enum(*SCENARIOS, name="inspection_status_code", native_enum=False),
            nullable=True,
        ),
    )
    op.create_unique_constraint("uq_inspection_statuses_code", "inspection_statuses", ["code"])
    op.add_column(
        "projects",
        sa.Column(
            "code",
            sa.Enum(*PROJECT_CODES, name="project_code", native_enum=False),
            nullable=True,
        ),
    )
    op.create_unique_constraint("uq_projects_code", "projects", ["code"])

    _rename_project(
        "ак",
        "Тандер, АГРОКОНТАРКТ/МАФ",
        "тандер, агроконтаркт/маф",
        "thunder_agro_maf",
    )
    _merge_watermelon_projects()
    _rename_project(
        "св",
        "Тандер, САМОВЫВОЗ",
        "тандер, самовывоз",
        "thunder_self_pickup",
    )
    _rename_project(
        "х5 св",
        "Х5 САМОВЫВОЗ",
        "х5 самовывоз",
        "x5_self_pickup",
    )
    _rename_project(
        "х5 техмп",
        "Х5 ТЕХМП RVI",
        "х5 техмп rvi",
        "x5_techmp_rvi",
    )
    _upsert_statuses()
    # Keep unknown historical projects and their relations, but hide them from
    # new V2 records whose scenarios depend on a stable project code.
    op.execute(sa.text("UPDATE projects SET active = FALSE WHERE code IS NULL"))

    op.create_table(
        "reference_uploads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "uploaded_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("row_count >= 0", name="ck_reference_uploads_row_count"),
        sa.CheckConstraint("warning_count >= 0", name="ck_reference_uploads_warning_count"),
    )
    op.create_index("ix_reference_uploads_active", "reference_uploads", ["active"])
    op.create_index(
        "uq_reference_uploads_single_active",
        "reference_uploads",
        ["active"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    op.create_index("ix_reference_uploads_sha256", "reference_uploads", ["sha256"])
    op.create_table(
        "reference_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "upload_id",
            sa.Integer(),
            sa.ForeignKey("reference_uploads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("order_number_raw", sa.String(64), nullable=False),
        sa.Column("order_number_normalized", sa.String(64), nullable=False),
        sa.Column("supplier_name", sa.String(255), nullable=True),
        sa.UniqueConstraint(
            "upload_id", "order_number_normalized", name="uq_reference_orders_upload_order"
        ),
    )
    op.create_index(
        "ix_reference_orders_lookup",
        "reference_orders",
        ["upload_id", "order_number_normalized"],
    )

    op.add_column(
        "inspections",
        sa.Column(
            "scenario",
            sa.Enum(*SCENARIOS, name="inspection_scenario", native_enum=False),
            nullable=True,
        ),
    )
    op.add_column("inspections", sa.Column("vehicle_number", sa.String(64), nullable=True))
    op.execute(
        sa.text(
            "UPDATE inspections SET scenario = COALESCE((SELECT code FROM inspection_statuses "
            "WHERE inspection_statuses.id = inspections.status_id), 'next')"
        )
    )
    op.alter_column("inspections", "scenario", nullable=False)
    op.alter_column("inspections", "supplier_id", existing_type=sa.Integer(), nullable=True)

    op.add_column(
        "inspection_order_numbers",
        sa.Column("supplier_name_snapshot", sa.String(255), nullable=True),
    )
    op.add_column(
        "inspection_order_numbers",
        sa.Column(
            "reconciliation_state",
            sa.Enum(
                *RECONCILIATION_STATES,
                name="reconciliation_state",
                native_enum=False,
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "inspection_order_numbers",
        sa.Column(
            "reference_upload_id",
            sa.Integer(),
            sa.ForeignKey("reference_uploads.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.execute(
        sa.text(
            "UPDATE inspection_order_numbers SET supplier_name_snapshot = ("
            "SELECT suppliers.name FROM inspections "
            "JOIN suppliers ON suppliers.id = inspections.supplier_id "
            "WHERE inspections.id = inspection_order_numbers.inspection_id), "
            "reconciliation_state = 'legacy'"
        )
    )
    op.alter_column("inspection_order_numbers", "reconciliation_state", nullable=False)


def downgrade() -> None:
    op.drop_column("inspection_order_numbers", "reference_upload_id")
    op.drop_column("inspection_order_numbers", "reconciliation_state")
    op.drop_column("inspection_order_numbers", "supplier_name_snapshot")
    connection = op.get_bind()
    fallback_supplier_id = connection.execute(
        sa.text("SELECT id FROM suppliers ORDER BY id LIMIT 1")
    ).scalar_one_or_none()
    if fallback_supplier_id is None:
        fallback_supplier_id = connection.execute(
            sa.text(
                "INSERT INTO suppliers "
                "(name, normalized_name, status, created_at, updated_at) VALUES "
                "('Поставщик не определён (откат)', "
                "'поставщик не определён (откат)', 'inactive', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) RETURNING id"
            )
        ).scalar_one()
    connection.execute(
        sa.text("UPDATE inspections SET supplier_id = :supplier_id WHERE supplier_id IS NULL"),
        {"supplier_id": fallback_supplier_id},
    )
    op.alter_column("inspections", "supplier_id", existing_type=sa.Integer(), nullable=False)
    op.drop_column("inspections", "vehicle_number")
    op.drop_column("inspections", "scenario")
    op.drop_index("ix_reference_orders_lookup", table_name="reference_orders")
    op.drop_table("reference_orders")
    op.drop_index("ix_reference_uploads_sha256", table_name="reference_uploads")
    op.drop_index("uq_reference_uploads_single_active", table_name="reference_uploads")
    op.drop_index("ix_reference_uploads_active", table_name="reference_uploads")
    op.drop_table("reference_uploads")
    op.drop_constraint("uq_projects_code", "projects", type_="unique")
    op.drop_column("projects", "code")
    op.drop_constraint("uq_inspection_statuses_code", "inspection_statuses", type_="unique")
    op.drop_column("inspection_statuses", "code")
