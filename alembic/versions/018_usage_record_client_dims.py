"""018: usage_record client/tool dimensions.

``usage_record`` has had the right shape since Spec 022 (tenant, api_key,
endpoint, method, status) but no writer — the table has never received a row.
``UsageMiddleware`` starts filling it, and these four columns are what make the
rows answer the question that motivated them: *which surface called, and which
MCP tool*.

``call_id`` deserves a note. One MCP tool call can fan out to several HTTP
requests — ``humetric_health`` issues three (``/healthz``, ``/healthz/db``,
``/healthz/worker``). Counting rows would report three tool calls where the
user made one. Reports therefore use ``COUNT(DISTINCT call_id)`` for tool calls
and ``COUNT(*)`` for HTTP requests.

All columns are nullable: rows written before this migration do not exist, but
rows from clients that do not identify themselves (plain curl, the dashboard)
legitimately have no client/tool, and NULL says that better than a sentinel.

RLS is untouched — ``usage_record`` already carries the ``tenant_isolation``
policy from the initial schema.

Revision ID: 018
Revises: 017
Create Date: 2026-08-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None

_TABLE = "usage_record"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("client", sa.String(16), nullable=True))
    op.add_column(_TABLE, sa.Column("tool_name", sa.String(64), nullable=True))
    op.add_column(_TABLE, sa.Column("call_id", sa.String(64), nullable=True))
    op.add_column(_TABLE, sa.Column("duration_ms", sa.Integer(), nullable=True))

    # Reports slice on exactly these two axes: per-key over a date range, and
    # per-channel over a date range.
    op.create_index(
        "ix_usage_record_key_time", _TABLE, ["tenant_id", "api_key_id", "created_at"]
    )
    op.create_index(
        "ix_usage_record_client_time", _TABLE, ["tenant_id", "client", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_usage_record_client_time", table_name=_TABLE)
    op.drop_index("ix_usage_record_key_time", table_name=_TABLE)

    op.drop_column(_TABLE, "duration_ms")
    op.drop_column(_TABLE, "call_id")
    op.drop_column(_TABLE, "tool_name")
    op.drop_column(_TABLE, "client")
