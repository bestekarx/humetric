"""SQLAlchemy ORM models — Humetric Phase 0 (Spec 021) + Signal/UsageRecord (Spec 022) + MetricPack (Spec 023) + Tenant registration/Stripe (Spec 026).

10 tables: tenant, entity, entity_metric, api_key, consent, audit_log, signal, usage_record,
metric_pack, task.
RLS: every tenant-scoped table has a tenant_id FK + RLS policy (defined in migrations).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .. import config
from .database import Base

_now = lambda: datetime.now(timezone.utc)  # noqa: E731

# The pgvector column size must match the runtime EMBED_DIM — otherwise the
# pgvector adapter raises an error when validating the inserted vector against
# the column size. DB column is vector(1024) (Voyage/Cohere default).
EMBED_DIM = config.EMBED_DIM


def _tenant_fk() -> Mapped[int]:
    return mapped_column(
        BigInteger, ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )


class Tenant(Base):
    __tablename__ = "tenant"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="active"
    )
    embedding_provider: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="voyage"
    )
    llm_provider: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="anthropic"
    )
    monthly_signal_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entity_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    anthropic_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    voyage_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    openai_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    google_ai_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    deepseek_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subscription_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="inactive"
    )
    tier: Mapped[str] = mapped_column(String(20), nullable=False, server_default="free")
    subscription_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (
        UniqueConstraint("code", name="uq_tenant_code"),
    )


class Entity(Base):
    __tablename__ = "entity"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tenant.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    fields: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    free_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[list | None] = mapped_column(Vector(EMBED_DIM), nullable=True)
    embedding_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="active"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=_now
    )
    embedding_pending: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    metrics: Mapped[list["EntityMetric"]] = relationship(
        "EntityMetric", back_populates="entity", cascade="all, delete-orphan"
    )
    signals: Mapped[list["Signal"]] = relationship(
        "Signal", back_populates="entity", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'archived')", name="ck_entity_status"
        ),
        Index("ix_entity_tenant_id", "tenant_id"),
        Index("ix_entity_type", "entity_type"),
    )


class EntityMetric(Base):
    __tablename__ = "entity_metric"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tenant_id: Mapped[int] = _tenant_fk()
    metric_key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    signal_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    trace_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schema_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewer_override: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    extraction_raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    review_status: Mapped[str | None] = mapped_column(String(20), nullable=True)

    entity: Mapped["Entity"] = relationship("Entity", back_populates="metrics")

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "entity_id"],
            ["entity.tenant_id", "entity.id"],
            ondelete="CASCADE",
            name="fk_entity_metric_entity",
        ),
        UniqueConstraint("tenant_id", "entity_id", "metric_key", name="uq_entity_metric_key"),
        CheckConstraint("value BETWEEN -1 AND 1", name="ck_entity_metric_value"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_entity_metric_confidence"),
        CheckConstraint("source_count >= 1", name="ck_entity_metric_source"),
        Index("ix_entity_metric_tenant_id", "tenant_id"),
        Index("ix_entity_metric_key", "metric_key"),
    )


class Signal(Base):
    """Signal processing record (Spec 022)."""
    __tablename__ = "signal"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[int] = _tenant_fk()
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="received")
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    pack_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pack_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    entity: Mapped["Entity"] = relationship("Entity", back_populates="signals")

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "entity_id"],
            ["entity.tenant_id", "entity.id"],
            ondelete="CASCADE",
            name="fk_signal_entity",
        ),
        CheckConstraint(
            "status IN ('received', 'processing', 'completed', 'failed')",
            name="ck_signal_status",
        ),
        Index("ix_signal_entity", "entity_id"),
        Index("ix_signal_tenant", "tenant_id"),
        Index("ix_signal_external_id", "tenant_id", "external_id"),
    )


class UsageRecord(Base):
    """API usage record (Spec 022)."""
    __tablename__ = "usage_record"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = _tenant_fk()
    api_key_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("api_key.id", ondelete="SET NULL"), nullable=True
    )
    endpoint: Mapped[str] = mapped_column(String(100), nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (
        Index("ix_usage_record_tenant", "tenant_id"),
        Index("ix_usage_record_api_key", "api_key_id"),
        Index("ix_usage_record_created_at", "created_at"),
    )


class MeteringRecord(Base):
    """Daily usage counter — signal/LLM/embedding (Spec 026)."""
    __tablename__ = "metering_record"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = _tenant_fk()
    date: Mapped[date] = mapped_column(nullable=False)
    signal_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    llm_token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "date", name="uq_metering_tenant_date"),
        Index("ix_metering_tenant", "tenant_id"),
    )


class ApiKey(Base):
    __tablename__ = "api_key"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = _tenant_fk()
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scopes: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_api_key_tenant_id", "tenant_id"),
        Index("ix_api_key_key_hash", "key_hash"),
        CheckConstraint("prefix IN ('hm_live', 'hm_test')", name="ck_api_key_prefix"),
    )


class Consent(Base):
    __tablename__ = "consent"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = _tenant_fk()
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="granted"
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "entity_id"],
            ["entity.tenant_id", "entity.id"],
            ondelete="CASCADE",
            name="fk_consent_entity",
        ),
        CheckConstraint(
            "status IN ('granted', 'revoked', 'expired')", name="ck_consent_status"
        ),
        Index("ix_consent_entity", "tenant_id", "entity_id", "scope"),
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = _tenant_fk()
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    api_key_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (
        Index("ix_audit_log_tenant_id", "tenant_id"),
        Index("ix_audit_log_action", "action"),
        Index("ix_audit_log_entity", "entity_id"),
    )


class MetricPack(Base):
    """Metric Pack definition (Spec 023)."""
    __tablename__ = "metric_pack"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = _tenant_fk()
    pack_key: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=_now
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "pack_key", name="uq_metric_pack_key"),
        Index("ix_metric_pack_tenant", "tenant_id"),
        Index("ix_metric_pack_active", "tenant_id", "is_active"),
    )


class Task(Base):
    """Async processing queue (Spec 024)."""
    __tablename__ = "task"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = _tenant_fk()
    signal_id: Mapped[str | None] = mapped_column(
        String(100), ForeignKey("signal.id", ondelete="SET NULL"), nullable=True
    )
    task_type: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="signal_process"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="queued"
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_task_status",
        ),
        CheckConstraint(
            "task_type IN ('signal_process', 're_embed', 'lakehouse_export')",
            name="ck_task_type",
        ),
        Index("ix_task_tenant", "tenant_id"),
        Index("ix_task_signal", "signal_id"),
        Index("ix_task_status", "status"),
        Index("ix_task_next_retry", "status", "next_retry_at"),
    )


