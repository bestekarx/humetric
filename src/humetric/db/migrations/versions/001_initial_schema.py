"""initial schema — tenant, api_key, entity, entity_metric, signal, usage_record,
metering_record, consent, audit_log, metric_pack, task + RLS

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

    # ── 1. tenant ─────────────────────────────────────────────

    op.create_table(
        "tenant",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("embedding_provider", sa.String(64), nullable=False, server_default="voyage"),
        sa.Column("llm_provider", sa.String(64), nullable=False, server_default="anthropic"),
        sa.Column("monthly_signal_quota", sa.Integer(), nullable=True),
        sa.Column("entity_quota", sa.Integer(), nullable=True),
        sa.Column("anthropic_key_encrypted", sa.Text(), nullable=True),
        sa.Column("voyage_key_encrypted", sa.Text(), nullable=True),
        sa.Column("email", sa.String(255), nullable=True, unique=True),
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("stripe_customer_id", sa.String(255), nullable=True),
        sa.Column("subscription_status", sa.String(20), nullable=False, server_default="inactive"),
        sa.Column("tier", sa.String(20), nullable=False, server_default="free"),
        sa.Column("subscription_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_tenant_code"),
    )

    op.execute("INSERT INTO tenant (code, name) VALUES ('default', 'Default Tenant')")

    # ── 2. api_key ────────────────────────────────────────────

    op.create_table(
        "api_key",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("scopes", sa.dialects.postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column("is_revoked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("prefix IN ('hm_live', 'hm_test')", name="ck_api_key_prefix"),
    )
    op.create_index("ix_api_key_tenant_id", "api_key", ["tenant_id"])
    op.create_index("ix_api_key_key_hash", "api_key", ["key_hash"])

    # ── 3. entity ─────────────────────────────────────────────

    op.create_table(
        "entity",
        sa.Column("id", sa.String(128), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("fields", sa.dialects.postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("free_text", sa.Text(), nullable=True),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("embedding_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("embedding_pending", sa.Boolean(), nullable=False, server_default="false"),
        sa.PrimaryKeyConstraint("id", "tenant_id"),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_entity_status"),
    )
    op.create_index("ix_entity_tenant_id", "entity", ["tenant_id"])
    op.create_index("ix_entity_type", "entity", ["entity_type"])

    # ── 4. entity_metric ──────────────────────────────────────

    op.create_table(
        "entity_metric",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("entity_id", sa.String(128), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("metric_key", sa.String(128), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("signal_id", sa.String(100), nullable=True),
        sa.Column("trace_data", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "entity_id"],
            ["entity.tenant_id", "entity.id"],
            ondelete="CASCADE",
            name="fk_entity_metric_entity",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "entity_id", "metric_key", name="uq_entity_metric_key"),
        sa.CheckConstraint("value BETWEEN -1 AND 1", name="ck_entity_metric_value"),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_entity_metric_confidence"),
        sa.CheckConstraint("source_count >= 1", name="ck_entity_metric_source"),
    )
    op.create_index("ix_entity_metric_tenant_id", "entity_metric", ["tenant_id"])
    op.create_index("ix_entity_metric_key", "entity_metric", ["metric_key"])

    # ── 5. signal ─────────────────────────────────────────────

    op.create_table(
        "signal",
        sa.Column("id", sa.String(100), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=True),
        sa.Column("entity_id", sa.String(128), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("structured", sa.dialects.postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="received"),
        sa.Column("result", sa.dialects.postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("pack_key", sa.String(128), nullable=True),
        sa.Column("pack_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "entity_id"],
            ["entity.tenant_id", "entity.id"],
            ondelete="CASCADE",
            name="fk_signal_entity",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("status IN ('received', 'processing', 'completed', 'failed')", name="ck_signal_status"),
    )
    op.create_index("ix_signal_entity", "signal", ["entity_id"])
    op.create_index("ix_signal_tenant", "signal", ["tenant_id"])
    op.create_index("ix_signal_external_id", "signal", ["tenant_id", "external_id"])

    # ── 6. usage_record ───────────────────────────────────────

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

    # ── 7. metering_record ────────────────────────────────────

    op.create_table(
        "metering_record",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("signal_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("llm_token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "date", name="uq_metering_tenant_date"),
    )
    op.create_index("ix_metering_tenant", "metering_record", ["tenant_id"])

    # ── 8. consent ────────────────────────────────────────────

    op.create_table(
        "consent",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("entity_id", sa.String(128), nullable=False),
        sa.Column("scope", sa.String(128), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="granted"),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "entity_id"],
            ["entity.tenant_id", "entity.id"],
            ondelete="CASCADE",
            name="fk_consent_entity",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("status IN ('granted', 'revoked', 'expired')", name="ck_consent_status"),
    )
    op.create_index("ix_consent_entity", "consent", ["tenant_id", "entity_id", "scope"])

    # ── 9. audit_log ──────────────────────────────────────────

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(128), nullable=True),
        sa.Column("details", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("api_key_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_log_tenant_id", "audit_log", ["tenant_id"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_entity", "audit_log", ["entity_id"])

    # ── 10. metric_pack ────────────────────────────────────────

    op.create_table(
        "metric_pack",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pack_key", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("definition", sa.dialects.postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "pack_key", name="uq_metric_pack_key"),
    )
    op.create_index("ix_metric_pack_tenant", "metric_pack", ["tenant_id"])
    op.create_index("ix_metric_pack_active", "metric_pack", ["tenant_id", "is_active"])

    # ── 11. task ──────────────────────────────────────────────

    op.create_table(
        "task",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("signal_id", sa.String(100), sa.ForeignKey("signal.id", ondelete="SET NULL"), nullable=True),
        sa.Column("task_type", sa.String(32), nullable=False, server_default="signal_process"),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("payload", sa.dialects.postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')", name="ck_task_status"
        ),
        sa.CheckConstraint(
            "task_type IN ('signal_process', 're_embed')", name="ck_task_type"
        ),
    )
    op.create_index("ix_task_tenant", "task", ["tenant_id"])
    op.create_index("ix_task_signal", "task", ["signal_id"])
    op.create_index("ix_task_status", "task", ["status"])
    op.create_index("ix_task_next_retry", "task", ["status", "next_retry_at"])

    # ── RLS + grants ──────────────────────────────────────────

    op.execute("CREATE ROLE humetric_app WITH LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD 'humetric_app'")

    rls_tables = [
        "api_key", "entity", "entity_metric", "signal", "usage_record",
        "metering_record", "consent", "audit_log", "metric_pack", "task",
    ]
    for table_name in rls_tables:
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table_name} "
            f"USING (tenant_id = current_setting('app.tenant_id')::bigint) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id')::bigint)"
        )

    for table_name in rls_tables + ["tenant"]:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table_name} TO humetric_app")

    sequences = [
        "entity_metric_id_seq", "api_key_id_seq", "usage_record_id_seq",
        "metering_record_id_seq", "consent_id_seq", "audit_log_id_seq",
        "metric_pack_id_seq", "task_id_seq",
    ]
    for seq in sequences:
        try:
            op.execute(f"GRANT USAGE, SELECT ON SEQUENCE {seq} TO humetric_app")
        except Exception:
            pass

    # ── Seed API key ──────────────────────────────────────────

    import hashlib
    import secrets
    seed_key = f"hm_{secrets.token_hex(24)}"
    key_hash = hashlib.sha256(seed_key.encode()).hexdigest()
    op.execute(
        sa.text(
            "INSERT INTO api_key (tenant_id, prefix, key_hash, scopes) "
            "VALUES (:tid, :prefix, :hash, :scopes)"
        ).bindparams(
            tid=1, prefix="hm_live", hash=key_hash,
            scopes='["entities:write","entities:read","signals:write","signals:read","query","packs:read","packs:admin"]',
        )
    )
    print(f"\n  SEED API KEY (default tenant, all scopes): {seed_key}\n")


def downgrade():
    op.drop_table("task")
    op.drop_table("metric_pack")
    op.drop_table("audit_log")
    op.drop_table("consent")
    op.drop_table("metering_record")
    op.drop_table("usage_record")
    op.drop_table("signal")
    op.drop_table("entity_metric")
    op.drop_table("entity")
    op.drop_table("api_key")
    op.drop_table("tenant")
