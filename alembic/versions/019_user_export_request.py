"""019: user_export table + export_request task type.

Adds a tenant-facing "export my data" feature: a `user_export` row tracks a
requested raw CSV/JSON zip of the tenant's data (entities, metrics, metric
history, signals, usage/metering records), independent of the `task` queue
row that drives the actual background processing. A dedicated table is
needed (rather than relying on `task` alone) because the worker's cleanup
sweep needs a durable `expires_at` to delete files after
HUMETRIC_USER_EXPORT_RETENTION_DAYS, and nothing guarantees `task` rows are
retained that long.

Also extends the `task.ck_task_type` CHECK constraint with the new
`export_request` task type.

Revision ID: 019
Revises: 018
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None

_TABLE = "user_export"


def upgrade() -> None:
    # --- task: extend task_type CHECK constraint ---
    op.drop_constraint("ck_task_type", "task", type_="check")
    op.create_check_constraint(
        "ck_task_type",
        "task",
        "task_type IN ('signal_process', 're_embed', 'lakehouse_export', 'export_request')",
    )

    # --- user_export table ---
    op.create_table(
        _TABLE,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.BigInteger(),
                  sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.BigInteger(),
                  sa.ForeignKey("task.id", ondelete="SET NULL"), nullable=True),
        sa.Column("format", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("file_path", sa.String(500), nullable=True),
        sa.Column("recipient_email", sa.String(255), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_user_export_status",
        _TABLE,
        "status IN ('pending', 'processing', 'completed', 'failed')",
    )
    op.create_check_constraint(
        "ck_user_export_format",
        _TABLE,
        "format IN ('csv', 'json')",
    )
    op.create_index("ix_user_export_tenant", _TABLE, ["tenant_id"])
    op.create_index("ix_user_export_status", _TABLE, ["status"])
    op.create_index("ix_user_export_expires", _TABLE, ["expires_at"])

    # --- RLS for user_export ---
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {_TABLE} "
        f"USING (tenant_id = current_setting('app.tenant_id', true)::bigint) "
        f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::bigint)"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO humetric_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO humetric_app")


def downgrade() -> None:
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON {_TABLE} FROM humetric_app")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE}")
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_user_export_expires", table_name=_TABLE)
    op.drop_index("ix_user_export_status", table_name=_TABLE)
    op.drop_index("ix_user_export_tenant", table_name=_TABLE)
    op.drop_table(_TABLE)

    op.drop_constraint("ck_task_type", "task", type_="check")
    op.create_check_constraint(
        "ck_task_type",
        "task",
        "task_type IN ('signal_process', 're_embed', 'lakehouse_export')",
    )
