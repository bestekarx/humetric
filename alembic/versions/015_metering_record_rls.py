"""015: Enable RLS + humetric_app grant on metering_record.

metering_record (added in 006) never got the tenant_isolation policy or the
GRANT to humetric_app that every other tenant-scoped table received in
001-004 (and retroactively for `tenant` in 008). It is the only tenant table
without RLS, so once the API/worker connect through the restricted
humetric_app role (instead of the table owner), reads/writes against it fail
with permission denied, and — while it stayed ungranted — it also offered no
defense-in-depth against a missing tenant_id filter in application code.

Revision ID: 015
Revises: 014
Create Date: 2026-07-01
"""

from __future__ import annotations

from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None

_TABLE = "metering_record"


def upgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {_TABLE} "
        f"USING (tenant_id = current_setting('app.tenant_id', true)::bigint) "
        f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::bigint)"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO humetric_app")


def downgrade() -> None:
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON {_TABLE} FROM humetric_app")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE}")
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
