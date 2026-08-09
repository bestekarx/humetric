"""017: signal.occurred_at + entity_metric_history (append-only metric time series).

Two related gaps are closed here.

1. ``signal`` had no notion of *when the source text was produced* — only
   ``created_at`` (ingest time). Historical backfill therefore collapsed onto
   a single point in time.
2. ``entity_metric`` is unique per (tenant, entity, metric_key), so every
   signal overwrote the previous value. No trend, no backtest, no per-signal
   contribution analysis was possible.

``entity_metric_history`` records one row per metric write. Its
``recorded_at`` carries the signal's ``occurred_at``; the ``now()`` server
default is a safety net, not the intended source.

Existing rows are backfilled so charts are not empty on day one:
``signal.occurred_at`` from ``created_at``, and one seed history point per
current ``entity_metric`` row using ``last_updated``.

Revision ID: 017
Revises: 016
Create Date: 2026-08-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None

_TABLE = "entity_metric_history"


def upgrade() -> None:
    # --- (a) signal.occurred_at ---
    op.add_column(
        "signal", sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute("UPDATE signal SET occurred_at = created_at WHERE occurred_at IS NULL")
    op.create_index("ix_signal_occurred", "signal", ["tenant_id", "occurred_at"])

    # --- (b) entity_metric_history ---
    op.create_table(
        _TABLE,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.BigInteger(),
                  sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_id", sa.String(128), nullable=False),
        sa.Column("metric_key", sa.String(128), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("prev_value", sa.Float(), nullable=True),
        sa.Column("signal_id", sa.String(100), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("source_span", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "entity_id"],
            ["entity.tenant_id", "entity.id"],
            ondelete="CASCADE",
            name="fk_entity_metric_history_entity",
        ),
        sa.CheckConstraint("value BETWEEN -1 AND 1", name="ck_emh_value"),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_emh_confidence"),
        sa.CheckConstraint("source_count >= 1", name="ck_emh_source"),
    )
    op.create_index(
        "ix_emh_lookup", _TABLE, ["tenant_id", "entity_id", "metric_key", "recorded_at"]
    )
    op.create_index("ix_emh_signal", _TABLE, ["tenant_id", "signal_id"])

    # --- RLS (see 015 for why the grants matter) ---
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {_TABLE} "
        f"USING (tenant_id = current_setting('app.tenant_id', true)::bigint) "
        f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::bigint)"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO humetric_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO humetric_app")

    # --- seed one history point per existing metric ---
    op.execute(
        f"INSERT INTO {_TABLE} "
        "(tenant_id, entity_id, metric_key, value, confidence, source_count, "
        " prev_value, signal_id, model, recorded_at) "
        "SELECT tenant_id, entity_id, metric_key, value, confidence, source_count, "
        "       NULL, signal_id, model, last_updated "
        "FROM entity_metric"
    )


def downgrade() -> None:
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON {_TABLE} FROM humetric_app")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE}")
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_emh_signal", table_name=_TABLE)
    op.drop_index("ix_emh_lookup", table_name=_TABLE)
    op.drop_table(_TABLE)

    op.drop_index("ix_signal_occurred", table_name="signal")
    op.drop_column("signal", "occurred_at")
