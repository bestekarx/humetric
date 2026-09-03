"""020: llm_call_record table — per-pack/signal/model token usage.

Adds granular LLM call tracking so "how many tokens did pack X burn, over how
many signals, on which model" can be answered — today MeteringRecord only
tracks a daily tenant-wide token total, with no pack/signal/model dimension.
MeteringRecord is untouched and remains the source for /v1/tenant/dashboard.

Revision ID: 020
Revises: 019
Create Date: 2026-08-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None

_TABLE = "llm_call_record"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.BigInteger(),
                  sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("signal_id", sa.String(100), nullable=True),
        sa.Column("pack_key", sa.String(128), nullable=True),
        sa.Column("pack_version", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_llm_call_record_tenant_pack", _TABLE, ["tenant_id", "pack_key"])
    op.create_index("ix_llm_call_record_tenant_created", _TABLE, ["tenant_id", "created_at"])

    # --- RLS ---
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

    op.drop_index("ix_llm_call_record_tenant_created", table_name=_TABLE)
    op.drop_index("ix_llm_call_record_tenant_pack", table_name=_TABLE)
    op.drop_table(_TABLE)
