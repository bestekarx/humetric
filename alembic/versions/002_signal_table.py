"""002: signal + usage_record tablolari + embedding_metni (Spec 022).

Revision ID: 002
Revises: 001
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None

_SIGNAL_TABLES = ["signal", "usage_record"]


def upgrade() -> None:
    # --- entity: add embedding_metni column (Spec 022) ---
    op.add_column("entity", sa.Column("embedding_metni", sa.Text(), nullable=True))

    # --- signal ---
    op.create_table(
        "signal",
        sa.Column("id", sa.String(100), primary_key=True, nullable=False),
        sa.Column("tenant_id", sa.BigInteger(),
                  sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=True),
        sa.Column("entity_id", sa.String(128),
                  sa.ForeignKey("entity.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("structured", sa.dialects.postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'")),
        sa.Column("status", sa.String(20), nullable=False, server_default="received"),
        sa.Column("result", sa.dialects.postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('received', 'processing', 'completed', 'failed')",
            name="ck_signal_status",
        ),
    )
    op.create_index("ix_signal_entity", "signal", ["entity_id"])
    op.create_index("ix_signal_tenant", "signal", ["tenant_id"])
    op.create_index("ix_signal_external_id", "signal", ["tenant_id", "external_id"])

    # --- usage_record ---
    op.create_table(
        "usage_record",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.BigInteger(),
                  sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("api_key_id", sa.BigInteger(),
                  sa.ForeignKey("api_key.id", ondelete="SET NULL"), nullable=True),
        sa.Column("endpoint", sa.String(100), nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_usage_record_tenant", "usage_record", ["tenant_id"])
    op.create_index("ix_usage_record_api_key", "usage_record", ["api_key_id"])
    op.create_index("ix_usage_record_created_at", "usage_record", ["created_at"])

    # --- RLS for new tables ---
    for tablo in _SIGNAL_TABLES:
        op.execute(f"ALTER TABLE {tablo} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {tablo} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {tablo} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::bigint) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::bigint)"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tablo} TO humetric_app")

    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO humetric_app")


def downgrade() -> None:
    for tablo in reversed(_SIGNAL_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {tablo}")
        op.execute(f"ALTER TABLE {tablo} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {tablo} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_usage_record_created_at", table_name="usage_record")
    op.drop_index("ix_usage_record_api_key", table_name="usage_record")
    op.drop_index("ix_usage_record_tenant", table_name="usage_record")
    op.drop_table("usage_record")

    op.drop_index("ix_signal_external_id", table_name="signal")
    op.drop_index("ix_signal_tenant", table_name="signal")
    op.drop_index("ix_signal_entity", table_name="signal")
    op.drop_table("signal")

    op.drop_column("entity", "embedding_metni")
