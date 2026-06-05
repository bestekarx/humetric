"""006: tenant self-service registration + Stripe billing columns (Spec 026).

Revision ID: 006
Revises: 005
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant", sa.Column(
        "email", sa.String(255), nullable=True,
    ))
    op.create_unique_constraint("uq_tenant_email", "tenant", ["email"])
    op.add_column("tenant", sa.Column(
        "email_verified", sa.Boolean(), nullable=False, server_default="false",
    ))
    op.add_column("tenant", sa.Column(
        "password_hash", sa.String(255), nullable=True,
    ))
    op.add_column("tenant", sa.Column(
        "stripe_customer_id", sa.String(255), nullable=True,
    ))
    op.create_index(
        "idx_tenant_stripe_customer", "tenant", ["stripe_customer_id"],
        unique=False, postgresql_where=sa.text("stripe_customer_id IS NOT NULL"),
    )
    op.add_column("tenant", sa.Column(
        "subscription_status", sa.String(20), nullable=False, server_default="inactive",
    ))
    op.create_check_constraint(
        "ck_tenant_subscription_status",
        "tenant",
        sa.text("subscription_status IN ('inactive', 'active', 'past_due', 'canceled', 'trialing')"),
    )
    op.add_column("tenant", sa.Column(
        "tier", sa.String(20), nullable=False, server_default="free",
    ))
    op.create_check_constraint(
        "ck_tenant_tier",
        "tenant",
        sa.text("tier IN ('free', 'pro', 'enterprise')"),
    )
    op.add_column("tenant", sa.Column(
        "subscription_end", sa.DateTime(timezone=True), nullable=True,
    ))
    op.create_table(
        "metering_record",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tarih", sa.Date(), nullable=False),
        sa.Column("sinyal_sayisi", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("llm_token_sayisi", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding_sayisi", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "tarih", name="uq_metering_tenant_tarih"),
    )
    op.create_index("ix_metering_tenant", "metering_record", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("metering_record")
    op.drop_constraint("ck_tenant_tier", "tenant")
    op.drop_column("tenant", "subscription_end")
    op.drop_column("tenant", "tier")
    op.drop_constraint("ck_tenant_subscription_status", "tenant")
    op.drop_column("tenant", "subscription_status")
    op.drop_index("idx_tenant_stripe_customer", "tenant")
    op.drop_column("tenant", "stripe_customer_id")
    op.drop_column("tenant", "password_hash")
    op.drop_column("tenant", "email_verified")
    op.drop_constraint("uq_tenant_email", "tenant")
    op.drop_column("tenant", "email")
