"""022: make every tenant_isolation policy tolerate a blank app.tenant_id.

get_tenant_db() clears the GUC on teardown with set_config('app.tenant_id', '',
false). PostgreSQL has no way to unset a GUC back to NULL — set_config(.., NULL,
..) also leaves an empty string — so the connection returns to the pool holding
''. The next non-tenant session on that connection (get_db: /healthz/worker and
friends) then evaluates the policy as ''::bigint and the query aborts with
InvalidTextRepresentationError instead of returning zero rows.

This was live: GET /healthz/worker answered 500 on every request that reused a
connection previously checked out by a tenant session.

Wrapping current_setting in NULLIF turns the blank back into NULL, so the
comparison is NULL and the policy stays fail-closed — no rows, no error. Only
the policy expressions change; RLS enable/force flags and grants are untouched.

Revision ID: 022
Revises: 021
Create Date: 2026-09-04
"""

from __future__ import annotations

from alembic import op

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None

# Every table carrying a tenant_id and a tenant_isolation policy.
_TABLES = [
    "api_key",
    "audit_log",
    "consent",
    "entity",
    "entity_metric",
    "entity_metric_history",
    "llm_call_record",
    "metering_record",
    "metric_pack",
    "signal",
    "task",
    "usage_record",
    "user_export",
]

_SAFE = "NULLIF(current_setting('app.tenant_id', true), '')::bigint"
_OLD = "current_setting('app.tenant_id', true)::bigint"


def _recreate_policies(expr: str) -> None:
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = {expr}) "
            f"WITH CHECK (tenant_id = {expr})"
        )


def upgrade() -> None:
    _recreate_policies(_SAFE)


def downgrade() -> None:
    _recreate_policies(_OLD)
