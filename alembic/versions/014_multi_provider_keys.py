"""014: Add multi-provider BYO key columns to tenant.

Adds openai_key_encrypted, google_ai_key_encrypted, deepseek_key_encrypted
nullable TEXT columns so tenants can store encrypted API keys for each LLM
provider (OpenAI, Google AI, DeepSeek) independently of the existing
anthropic_key_encrypted / voyage_key_encrypted columns.

The llm_provider column already exists (default 'anthropic') and controls
which provider processes the tenant's signals.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant", sa.Column("openai_key_encrypted", sa.Text(), nullable=True))
    op.add_column("tenant", sa.Column("google_ai_key_encrypted", sa.Text(), nullable=True))
    op.add_column("tenant", sa.Column("deepseek_key_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenant", "deepseek_key_encrypted")
    op.drop_column("tenant", "google_ai_key_encrypted")
    op.drop_column("tenant", "openai_key_encrypted")
