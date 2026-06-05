"""initial schema — tenant, api_key, entity, entity_metric, usage_record + RLS

Revision ID: 001
Create Date: 2026-06-04
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "tenant",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("kod", sa.Text(), nullable=False),
        sa.Column("ad", sa.Text(), nullable=False),
        sa.Column("aktif", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kod", name="uq_tenant_kod"),
    )

    # Insert default tenant
    op.execute("INSERT INTO tenant (kod, ad) VALUES ('default', 'Default Tenant')")

    for tablo in ("api_key", "entity", "entity_metric", "usage_record"):
        op.execute(f"CREATE ROLE humetric_app WITH LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD 'humetric_app'")
        break

    op.create_table(
        "api_key",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("key_prefix", sa.String(12), nullable=False),
        sa.Column("scopes", sa.dialects.postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash", name="uq_api_key_hash"),
    )
    op.create_index("ix_api_key_tenant_id", "api_key", ["tenant_id"])
    op.create_index("ix_api_key_key_hash", "api_key", ["key_hash"])

    op.create_table(
        "entity",
        sa.Column("id", sa.String(100), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("fields", sa.dialects.postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("free_text", sa.Text(), nullable=True),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("embedding_metni", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="'active'"),
        sa.Column("meta", sa.dialects.postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_entity_tenant_id", "entity", ["tenant_id"])
    op.create_index("ix_entity_type", "entity", ["tenant_id", "entity_type"])
    op.create_index("ix_entity_status", "entity", ["tenant_id", "status"])

    op.create_table(
        "entity_metric",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_id", sa.String(100), sa.ForeignKey("entity.id", ondelete="CASCADE"), nullable=False),
        sa.Column("metric_key", sa.String(100), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("source_signal_id", sa.String(100), nullable=True),
        sa.Column("trace_data", sa.dialects.postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entity_id", "metric_key", name="uq_entity_metric_key"),
    )
    op.create_index("ix_entity_metric_entity", "entity_metric", ["entity_id"])
    op.create_index("ix_entity_metric_tenant", "entity_metric", ["tenant_id"])

    op.create_table(
        "usage_record",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("api_key_id", sa.BigInteger(), sa.ForeignKey("api_key.id", ondelete="SET NULL"), nullable=True),
        sa.Column("endpoint", sa.String(100), nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usage_record_tenant", "usage_record", ["tenant_id"])
    op.create_index("ix_usage_record_api_key", "usage_record", ["api_key_id"])
    op.create_index("ix_usage_record_created_at", "usage_record", ["created_at"])

    # RLS: enable + force + policy for tables that need tenant isolation
    rls_tablolar = ["api_key", "entity", "entity_metric", "usage_record"]
    for tablo in rls_tablolar:
        op.execute(f"ALTER TABLE {tablo} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {tablo} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {tablo} "
            f"USING (tenant_id = current_setting('app.tenant_id')::bigint) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id')::bigint)"
        )

    # Grant privileges to app role
    for tablo in rls_tablolar + ["tenant"]:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tablo} TO humetric_app")

    for seq in ["entity_metric_id_seq", "api_key_id_seq", "usage_record_id_seq"]:
        try:
            op.execute(f"GRANT USAGE, SELECT ON SEQUENCE {seq} TO humetric_app")
        except Exception:
            pass

    # Insert a seed API key for the default tenant (full scopes, no expiry)
    import hashlib, secrets
    seed_key = f"hm_{secrets.token_hex(24)}"
    key_hash = hashlib.sha256(seed_key.encode()).hexdigest()
    key_prefix = seed_key[:10]
    op.execute(
        sa.text(
            "INSERT INTO api_key (tenant_id, name, key_hash, key_prefix, scopes, created_by) "
            "VALUES (:tid, :name, :hash, :prefix, :scopes, :by)"
        ).bindparams(
            tid=1, name="seed-key", hash=key_hash, prefix=key_prefix,
            scopes='["entities:write","entities:read","signals:write","signals:read","query"]',
            by="seed"
        )
    )
    print(f"\n  SEED API KEY (default tenant, tum scope'lar): {seed_key}\n")


def downgrade():
    op.drop_table("usage_record")
    op.drop_table("entity_metric")
    op.drop_table("entity")
    op.drop_table("api_key")
    op.drop_table("tenant")
