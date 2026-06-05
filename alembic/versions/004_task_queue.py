"""004: task tablosu + entity.embedding_pending + signal idempotency unique (Spec 024).

Revision ID: 004
Revises: 003
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None

_TASK_TABLE = "task"


def upgrade() -> None:
    # --- task table ---
    op.create_table(
        _TASK_TABLE,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.BigInteger(),
                  sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("signal_id", sa.String(100),
                  sa.ForeignKey("signal.id", ondelete="SET NULL"), nullable=True),
        sa.Column("task_type", sa.String(32), nullable=False, server_default="signal_process"),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("payload", sa.dialects.postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'")),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_task_tenant", _TASK_TABLE, ["tenant_id"])
    op.create_index("ix_task_signal", _TASK_TABLE, ["signal_id"])
    op.create_index("ix_task_status", _TASK_TABLE, ["status"])
    op.create_index("ix_task_next_retry", _TASK_TABLE, ["status", "next_retry_at"])

    # --- entity: add embedding_pending ---
    op.add_column("entity", sa.Column(
        "embedding_pending", sa.Boolean(), nullable=False,
        server_default=sa.text("false"),
    ))

    # --- signal: add unique constraint for idempotency ---
    # Drop existing non-unique index first, then create unique constraint
    op.execute("DROP INDEX IF EXISTS ix_signal_external_id")
    op.create_unique_constraint(
        "uq_signal_idempotency",
        "signal",
        ["tenant_id", "external_id", "entity_id"],
    )
    op.create_index("ix_signal_external_id", "signal", ["external_id"])

    # --- RLS for task ---
    op.execute(f"ALTER TABLE {_TASK_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TASK_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {_TASK_TABLE} "
        f"USING (tenant_id = current_setting('app.tenant_id', true)::bigint) "
        f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::bigint)"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TASK_TABLE} TO humetric_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO humetric_app")


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TASK_TABLE}")
    op.execute(f"ALTER TABLE {_TASK_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TASK_TABLE} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_task_next_retry", table_name=_TASK_TABLE)
    op.drop_index("ix_task_status", table_name=_TASK_TABLE)
    op.drop_index("ix_task_signal", table_name=_TASK_TABLE)
    op.drop_index("ix_task_tenant", table_name=_TASK_TABLE)
    op.drop_table(_TASK_TABLE)

    op.drop_column("entity", "embedding_pending")

    op.drop_constraint("uq_signal_idempotency", "signal")
    op.create_index("ix_signal_external_id", "signal", ["tenant_id", "external_id"])
