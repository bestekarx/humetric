"""003: metric_pack tablosu + signal pack referans kolonlari (Spec 023).

Revision ID: 003
Revises: 002
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None

_PACK_TABLE = "metric_pack"


def upgrade() -> None:
    # --- metric_pack ---
    op.create_table(
        "metric_pack",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.BigInteger(),
                  sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pack_key", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("definition", sa.dialects.postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "pack_key", name="uq_metric_pack_key"),
    )
    op.create_index("ix_metric_pack_tenant", "metric_pack", ["tenant_id"])
    op.create_index("ix_metric_pack_active", "metric_pack", ["tenant_id", "is_active"])

    # Partial unique: tenant'da ayni entity_type sadece bir aktif pack'te olabilir
    op.execute(
        "CREATE UNIQUE INDEX uq_metric_pack_entity_type_active "
        "ON metric_pack (tenant_id, (definition->>'entity_type')) "
        "WHERE is_active = true"
    )

    # --- signal: add pack reference columns ---
    op.add_column("signal", sa.Column("pack_key", sa.String(128), nullable=True))
    op.add_column("signal", sa.Column("pack_version", sa.Integer(), nullable=True))

    # --- consent: add expires_at column ---
    op.add_column("consent", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))

    # --- RLS for metric_pack ---
    op.execute(f"ALTER TABLE {_PACK_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_PACK_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {_PACK_TABLE} "
        f"USING (tenant_id = current_setting('app.tenant_id', true)::bigint) "
        f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::bigint)"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_PACK_TABLE} TO humetric_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO humetric_app")


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_PACK_TABLE}")
    op.execute(f"ALTER TABLE {_PACK_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_PACK_TABLE} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_metric_pack_active", table_name="metric_pack")
    op.drop_index("ix_metric_pack_tenant", table_name="metric_pack")
    op.drop_table("metric_pack")

    op.drop_column("consent", "expires_at")
    op.drop_column("signal", "pack_version")
    op.drop_column("signal", "pack_key")
