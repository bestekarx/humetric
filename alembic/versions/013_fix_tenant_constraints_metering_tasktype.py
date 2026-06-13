"""013: Fix tenant constraints, rename metering_record columns, extend task_type.

Migration 011 renamed tenant.durum -> status but left the CHECK constraint
(ck_tenant_durum) with Turkish values 'aktif'/'pasif'/'askida' intact.
Because Postgres auto-updates the column reference in the expression the
constraint is now effectively `status IN ('aktif','pasif','askida')`, so
any INSERT/UPDATE that uses the English value 'active' is rejected.

This migration:
  - Drops ck_tenant_durum and migrates existing data to English values.
  - Sets the correct server_default ('active') on tenant.status.
  - Renames uq_tenant_kod -> uq_tenant_code (cosmetic, matches models.py).
  - Renames metering_record Turkish columns:
      tarih -> date, sinyal_sayisi -> signal_count,
      llm_token_sayisi -> llm_token_count, embedding_sayisi -> embedding_count
    and renames the unique constraint accordingly.
  - Extends the ck_task_type CHECK constraint to include 'lakehouse_export'.

Revision ID: 013
Revises: 012
Create Date: 2026-06-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── tenant: fix CHECK constraint left with Turkish values after 011 ───────
    op.drop_constraint("ck_tenant_durum", "tenant", type_="check")
    op.execute("UPDATE tenant SET status = 'active'   WHERE status = 'aktif'")
    op.execute("UPDATE tenant SET status = 'inactive' WHERE status = 'pasif'")
    op.execute("UPDATE tenant SET status = 'archived' WHERE status = 'askida'")
    op.alter_column("tenant", "status",
                    existing_type=sa.String(20),
                    server_default="active")

    # ── tenant: rename unique constraint (cosmetic, matches models.py) ────────
    op.drop_constraint("uq_tenant_kod", "tenant", type_="unique")
    op.create_unique_constraint("uq_tenant_code", "tenant", ["code"])

    # ── metering_record: Turkish -> English columns ───────────────────────────
    op.drop_constraint("uq_metering_tenant_tarih", "metering_record", type_="unique")
    op.alter_column("metering_record", "tarih",          new_column_name="date")
    op.alter_column("metering_record", "sinyal_sayisi",  new_column_name="signal_count")
    op.alter_column("metering_record", "llm_token_sayisi", new_column_name="llm_token_count")
    op.alter_column("metering_record", "embedding_sayisi", new_column_name="embedding_count")
    op.create_unique_constraint(
        "uq_metering_tenant_date", "metering_record", ["tenant_id", "date"]
    )

    # ── task: extend ck_task_type to include 'lakehouse_export' ──────────────
    op.execute("ALTER TABLE task DROP CONSTRAINT IF EXISTS ck_task_type")
    op.execute(
        "ALTER TABLE task ADD CONSTRAINT ck_task_type "
        "CHECK (task_type IN ('signal_process', 're_embed', 'lakehouse_export'))"
    )


def downgrade() -> None:
    # ── task ──────────────────────────────────────────────────────────────────
    op.execute("ALTER TABLE task DROP CONSTRAINT IF EXISTS ck_task_type")
    op.execute(
        "ALTER TABLE task ADD CONSTRAINT ck_task_type "
        "CHECK (task_type IN ('signal_process', 're_embed'))"
    )

    # ── metering_record ───────────────────────────────────────────────────────
    op.drop_constraint("uq_metering_tenant_date", "metering_record", type_="unique")
    op.alter_column("metering_record", "embedding_count",  new_column_name="embedding_sayisi")
    op.alter_column("metering_record", "llm_token_count",  new_column_name="llm_token_sayisi")
    op.alter_column("metering_record", "signal_count",     new_column_name="sinyal_sayisi")
    op.alter_column("metering_record", "date",             new_column_name="tarih")
    op.create_unique_constraint(
        "uq_metering_tenant_tarih", "metering_record", ["tenant_id", "tarih"]
    )

    # ── tenant ────────────────────────────────────────────────────────────────
    op.drop_constraint("uq_tenant_code", "tenant", type_="unique")
    op.create_unique_constraint("uq_tenant_kod", "tenant", ["code"])

    op.alter_column("tenant", "status",
                    existing_type=sa.String(20),
                    server_default="aktif")
    op.execute("UPDATE tenant SET status = 'aktif'  WHERE status = 'active'")
    op.execute("UPDATE tenant SET status = 'pasif'  WHERE status = 'inactive'")
    op.execute("UPDATE tenant SET status = 'askida' WHERE status = 'archived'")
    op.create_check_constraint(
        "ck_tenant_durum", "tenant",
        "status IN ('aktif', 'pasif', 'askida')"
    )
