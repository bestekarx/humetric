"""007: tenant updated_at column.

Revision ID: 007
Revises: 006
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant", sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=True,
    ))


def downgrade() -> None:
    op.drop_column("tenant", "updated_at")
