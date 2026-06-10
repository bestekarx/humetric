"""010: Eval/replay metadata columns (input_hash, prompt_hash, schema_hash, model, reviewer_override, extraction_raw, review_status).

Adds traceability columns to entity_metric and signal tables so every
metric row can be audited back to the exact prompt, schema, model, and
input that produced it. Also adds reviewer_override for human corrections
and review_status for metrics flagged as needs_review.

Revision ID: 010
Revises: 009
Create Date: 2026-06-09
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- entity_metric: eval metadata columns ---
    op.add_column("entity_metric", sa.Column("input_hash", sa.String(64), nullable=True))
    op.add_column("entity_metric", sa.Column("prompt_hash", sa.String(64), nullable=True))
    op.add_column("entity_metric", sa.Column("schema_hash", sa.String(64), nullable=True))
    op.add_column("entity_metric", sa.Column("model", sa.String(64), nullable=True))
    op.add_column("entity_metric", sa.Column("reviewer_override", JSONB, nullable=True))
    op.add_column("entity_metric", sa.Column("extraction_raw", JSONB, nullable=True))
    op.add_column("entity_metric", sa.Column("review_status", sa.String(20), nullable=True))

    # --- signal: input_hash ---
    op.add_column("signal", sa.Column("input_hash", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("entity_metric", "review_status")
    op.drop_column("entity_metric", "extraction_raw")
    op.drop_column("entity_metric", "reviewer_override")
    op.drop_column("entity_metric", "model")
    op.drop_column("entity_metric", "schema_hash")
    op.drop_column("entity_metric", "prompt_hash")
    op.drop_column("entity_metric", "input_hash")

    op.drop_column("signal", "input_hash")
