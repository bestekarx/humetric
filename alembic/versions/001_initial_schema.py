"""Initial schema: pgvector extension + 6 core tables + RLS + index'ler.

Table'lar: tenant, entity, entity_metric, api_key, consent, audit_log.
RLS: tum tenant'a-bagli tablolarda ENABLE + FORCE + tenant_isolation policy.
Roller: humetric (superuser) ve humetric_app (runtime, NOSUPERUSER).
Index'ler: ivfflat (entity.embedding cosine), GIN (entity.fields, entity.free_text).

Revision ID: 001
Revises:
Create Date: 2026-06-04
"""

from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision = "001"
down_revision = None
branch_labels = None
depends_on = None

_TENANT_TABLES = [
    "entity", "entity_metric", "api_key", "consent", "audit_log",
]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # --- tenant ---
    op.create_table(
        "tenant",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("kod", sa.String(64), nullable=False),
        sa.Column("ad", sa.String(255), nullable=False),
        sa.Column("durum", sa.String(20), nullable=False, server_default="aktif"),
        sa.Column("embedding_provider", sa.String(64), nullable=False, server_default="voyage"),
        sa.Column("llm_provider", sa.String(64), nullable=False, server_default="anthropic"),
        sa.Column("kota_sinyal_aylik", sa.Integer(), nullable=True),
        sa.Column("kota_entity", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "durum IN ('aktif', 'pasif', 'askida')", name="ck_tenant_durum"
        ),
        sa.UniqueConstraint("kod", name="uq_tenant_kod"),
    )

    # --- entity ---
    op.create_table(
        "entity",
        sa.Column("id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("tenant_id", sa.BigInteger(),
                  sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("fields", JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("free_text", sa.Text(), nullable=True),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'archived')", name="ck_entity_status"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_entity_tenant_id"),
    )
    op.create_index("ix_entity_tenant_id", "entity", ["tenant_id"])
    op.create_index("ix_entity_type", "entity", ["entity_type"])
    op.create_index("ix_entity_fields_gin", "entity", ["fields"], postgresql_using="gin")
    op.execute(
        "CREATE INDEX ix_entity_free_text_gin ON entity "
        "USING gin (to_tsvector('simple', coalesce(free_text, '')))"
    )

    # --- entity_metric ---
    op.create_table(
        "entity_metric",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("entity_id", sa.String(128),
                  sa.ForeignKey("entity.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(),
                  sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("metric_key", sa.String(128), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("signal_id", sa.String(100), nullable=True),
        sa.Column("trace_data", JSONB(), nullable=True),
        sa.CheckConstraint("value BETWEEN -1 AND 1", name="ck_entity_metric_value"),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_entity_metric_confidence"),
        sa.CheckConstraint("source_count >= 1", name="ck_entity_metric_source"),
        sa.UniqueConstraint("entity_id", "metric_key", name="uq_entity_metric_key"),
    )
    op.create_index("ix_entity_metric_tenant_id", "entity_metric", ["tenant_id"])
    op.create_index("ix_entity_metric_key", "entity_metric", ["metric_key"])

    # --- api_key ---
    op.create_table(
        "api_key",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.BigInteger(),
                  sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("scopes", JSONB(), nullable=False, server_default="[]"),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column("is_revoked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("prefix IN ('hm_live', 'hm_test')", name="ck_api_key_prefix"),
    )
    op.create_index("ix_api_key_tenant_id", "api_key", ["tenant_id"])
    op.create_index("ix_api_key_key_hash", "api_key", ["key_hash"])

    # --- consent ---
    op.create_table(
        "consent",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.BigInteger(),
                  sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_id", sa.String(128),
                  sa.ForeignKey("entity.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope", sa.String(128), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="granted"),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('granted', 'revoked', 'expired')", name="ck_consent_status"
        ),
    )
    op.create_index("ix_consent_entity", "consent", ["tenant_id", "entity_id", "scope"])

    # --- audit_log ---
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.BigInteger(),
                  sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(128), nullable=True),
        sa.Column("details", JSONB(), nullable=True),
        sa.Column("api_key_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_audit_log_tenant_id", "audit_log", ["tenant_id"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_entity", "audit_log", ["entity_id"])

    # --- ivfflat index (entity embedding) — sonra, embedding sütunu tamsa ---
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_entity_embedding_ivfflat "
        "ON entity USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )

    # --- RLS: humetric_app roll ve tenant_isolation policy ---
    # Password comes from HUMETRIC_APP_DB_PASSWORD; the weak default is for
    # local dev only. Production MUST set this env var on the migrate step,
    # or rotate afterwards with scripts/create_app_role.sql.
    # Strict charset: the value is embedded in a $$-quoted SQL block here and
    # in DATABASE_URL_APP without URL-encoding, so anything beyond URL-safe
    # characters is both an injection hazard and a broken connection string.
    app_password = os.environ.get("HUMETRIC_APP_DB_PASSWORD", "humetric_app")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", app_password):
        raise ValueError(
            "HUMETRIC_APP_DB_PASSWORD must be 1-128 chars of [A-Za-z0-9_-] "
            "(URL-safe, no quoting needed)."
        )
    op.execute(
        "DO $$ BEGIN "
        "  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'humetric_app') THEN "
        f"    CREATE ROLE humetric_app WITH LOGIN PASSWORD '{app_password}' NOSUPERUSER NOBYPASSRLS; "
        "  END IF; "
        "END $$"
    )

    for tablo in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {tablo} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {tablo} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {tablo} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::bigint) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::bigint)"
        )
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tablo} TO humetric_app"
        )

    op.execute(
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO humetric_app"
    )


def downgrade() -> None:
    for tablo in reversed(_TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {tablo}")
        op.execute(f"ALTER TABLE {tablo} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {tablo} DISABLE ROW LEVEL SECURITY")

    op.execute("DROP INDEX IF EXISTS ix_entity_embedding_ivfflat")

    op.drop_index("ix_audit_log_entity", table_name="audit_log")
    op.drop_index("ix_audit_log_action", table_name="audit_log")
    op.drop_index("ix_audit_log_tenant_id", table_name="audit_log")
    op.drop_table("audit_log")

    op.drop_index("ix_consent_entity", table_name="consent")
    op.drop_table("consent")

    op.drop_index("ix_api_key_key_hash", table_name="api_key")
    op.drop_index("ix_api_key_tenant_id", table_name="api_key")
    op.drop_table("api_key")

    op.drop_index("ix_entity_metric_key", table_name="entity_metric")
    op.drop_index("ix_entity_metric_tenant_id", table_name="entity_metric")
    op.drop_table("entity_metric")

    op.drop_index("ix_entity_free_text_gin", table_name="entity")
    op.drop_index("ix_entity_fields_gin", table_name="entity")
    op.drop_index("ix_entity_type", table_name="entity")
    op.drop_index("ix_entity_tenant_id", table_name="entity")
    op.drop_table("entity")

    op.drop_table("tenant")

    op.execute("DROP ROLE IF EXISTS humetric_app")
    op.execute("DROP EXTENSION IF EXISTS vector")
