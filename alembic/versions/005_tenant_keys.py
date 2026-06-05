"""005: tenant BYO-key columns — anthropic_key_encrypted, voyage_key_encrypted (Spec 025).

Revision ID: 005
Revises: 004
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant", sa.Column(
        "anthropic_key_encrypted", sa.Text(), nullable=True,
    ))
    op.add_column("tenant", sa.Column(
        "voyage_key_encrypted", sa.Text(), nullable=True,
    ))


def downgrade() -> None:
    op.drop_column("tenant", "voyage_key_encrypted")
    op.drop_column("tenant", "anthropic_key_encrypted")
