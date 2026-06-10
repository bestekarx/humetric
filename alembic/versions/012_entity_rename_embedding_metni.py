"""012: Rename entity.embedding_metni to embedding_text.

Revision ID: 012
Revises: 011
Create Date: 2026-06-10
"""
from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("entity", "embedding_metni", new_column_name="embedding_text")


def downgrade():
    op.alter_column("entity", "embedding_text", new_column_name="embedding_metni")
