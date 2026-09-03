"""021: context_hash — a third fingerprint tracking entity-context drift.

entity_metric/entity_metric_history already carry prompt_hash (prompt version)
and input_hash (signal text). Neither one covers the entity context injected
into the user message, so a context change between two runs is invisible to
replay comparisons. This adds a nullable, purely-additive context_hash column
to both tables; input_hash's definition is untouched so historical
comparisons keep working.

Revision ID: 021
Revises: 020
Create Date: 2026-08-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "entity_metric",
        sa.Column("context_hash", sa.String(64), nullable=True),
    )
    op.add_column(
        "entity_metric_history",
        sa.Column("context_hash", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("entity_metric_history", "context_hash")
    op.drop_column("entity_metric", "context_hash")
