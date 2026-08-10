"""017_tenant_trial: 3-month free Pro trial state on the tenant.

The site has offered this trial for a while (dashboard + /start-trial call),
but the server side was never in this repo: /v1/tenant/start-trial did not
exist and the dashboard returned no trial fields. The frontend reads
`trial_available` and treats a missing value as falsy, so every account — new
ones included — was shown "this account has already used its free trial".

Columns:
  trial_status      none | active | expired
  trial_started_at  when the tenant activated it
  trial_ends_at     when Pro access lapses back to free

IDEMPOTENT: databases provisioned by the deleted 016_analysis_session already
carry these columns, so every statement is guarded with IF NOT EXISTS. That
keeps this migration safe on both a fresh database and an already-drifted one.

Revision ID: 017_tenant_trial
Revises: 016_llm_jury
Create Date: 2026-08-09
"""

from __future__ import annotations

from alembic import op

revision = "017_tenant_trial"
down_revision = "016_llm_jury"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE tenant
            ADD COLUMN IF NOT EXISTS trial_status VARCHAR(20) NOT NULL DEFAULT 'none',
            ADD COLUMN IF NOT EXISTS trial_started_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMPTZ
        """
    )

    # Only active trials are ever swept for expiry, so a partial index keeps it
    # small regardless of how many tenants have already used theirs.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_tenant_trial_due
            ON tenant (trial_ends_at)
         WHERE trial_status = 'active'
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'ck_tenant_trial_status'
            ) THEN
                ALTER TABLE tenant ADD CONSTRAINT ck_tenant_trial_status
                    CHECK (trial_status IN ('none', 'active', 'expired'));
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE tenant DROP CONSTRAINT IF EXISTS ck_tenant_trial_status")
    op.execute("DROP INDEX IF EXISTS ix_tenant_trial_due")
    op.execute(
        """
        ALTER TABLE tenant
            DROP COLUMN IF EXISTS trial_ends_at,
            DROP COLUMN IF EXISTS trial_started_at,
            DROP COLUMN IF EXISTS trial_status
        """
    )
