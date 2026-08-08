"""016: tenant Pro trial columns.

Adds the three columns the self-service 3-month Pro trial needs:
trial_started_at / trial_ends_at (when it runs) and trial_status
(none | active | expired | converted) so an expired trial can be told
apart from one that converted to a paid Stripe subscription — the
expiry sweep must only downgrade the former.

Revision ID: 016
Revises: 015
Create Date: 2026-08-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant", sa.Column(
        "trial_started_at", sa.DateTime(timezone=True), nullable=True,
    ))
    op.add_column("tenant", sa.Column(
        "trial_ends_at", sa.DateTime(timezone=True), nullable=True,
    ))
    op.add_column("tenant", sa.Column(
        "trial_status", sa.String(20), nullable=False, server_default="none",
    ))
    # The expiry sweep scans for due trials across all tenants.
    op.create_index(
        "ix_tenant_trial_due",
        "tenant",
        ["trial_ends_at"],
        postgresql_where=sa.text("trial_status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("ix_tenant_trial_due", table_name="tenant")
    op.drop_column("tenant", "trial_status")
    op.drop_column("tenant", "trial_ends_at")
    op.drop_column("tenant", "trial_started_at")
