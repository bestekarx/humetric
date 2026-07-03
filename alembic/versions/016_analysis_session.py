"""016: Add analysis_session table (Metric Analyzer, Spec 027 Faz 1).

New tenant-scoped table backing the paid autonomous Metric Analyzer scan:
one-time Stripe payment -> Fable-driven schema/image/market analysis ->
findings + open questions -> up to 2 free refines -> pack creation.

Also extends the task.ck_task_type CHECK constraint with the new
'analysis_scan' task type, following the 013 pattern (DROP IF EXISTS + ADD).

Revision ID: 016
Revises: 015
Create Date: 2026-07-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None

_TABLE = "analysis_session"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "tenant_id", sa.BigInteger(),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column(
            "status", sa.String(32), nullable=False,
            server_default="pending_payment",
        ),
        sa.Column("artifacts", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("report", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("findings", postgresql.JSONB(), nullable=True),
        sa.Column("refine_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("pack_key", sa.String(128), nullable=True),
        sa.Column("checkout_session_id", sa.String(255), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending_payment', 'processing', 'findings_ready', 'completed', 'failed')",
            name="ck_analysis_session_status",
        ),
    )
    op.create_index("ix_analysis_session_tenant", _TABLE, ["tenant_id"])
    op.create_index("ix_analysis_session_status", _TABLE, ["tenant_id", "status"])

    # ── RLS (pattern from 004_task_queue.py) ──────────────────────────────
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {_TABLE} "
        f"USING (tenant_id = current_setting('app.tenant_id', true)::bigint) "
        f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::bigint)"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO humetric_app")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO humetric_app")

    # ── task: extend ck_task_type to include 'analysis_scan' ──────────────
    op.execute("ALTER TABLE task DROP CONSTRAINT IF EXISTS ck_task_type")
    op.execute(
        "ALTER TABLE task ADD CONSTRAINT ck_task_type "
        "CHECK (task_type IN ('signal_process', 're_embed', 'lakehouse_export', 'analysis_scan'))"
    )


def downgrade() -> None:
    # ── task ────────────────────────────────────────────────────────────────
    op.execute("DELETE FROM task WHERE task_type = 'analysis_scan'")
    op.execute("ALTER TABLE task DROP CONSTRAINT IF EXISTS ck_task_type")
    op.execute(
        "ALTER TABLE task ADD CONSTRAINT ck_task_type "
        "CHECK (task_type IN ('signal_process', 're_embed', 'lakehouse_export'))"
    )

    # ── analysis_session ───────────────────────────────────────────────────
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON {_TABLE} FROM humetric_app")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE}")
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_analysis_session_status", table_name=_TABLE)
    op.drop_index("ix_analysis_session_tenant", table_name=_TABLE)
    op.drop_table(_TABLE)
