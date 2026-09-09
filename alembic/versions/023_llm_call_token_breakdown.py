"""023: split llm_call_record's single total into input/output/cache tokens.

``llm_call_record`` has carried one ``token_count`` since 020 — input plus
output, added together. That number cannot answer either question this feature
exists for: *how much of the input was billed at full price* and *did the prompt
cache engage at all*. Four nullable columns split it apart.

``token_count`` keeps its meaning and its readers (``store.py``'s pack usage
report). Re-interpreting it would be a silent behaviour change; the new columns
sit beside it instead.

All four are nullable on purpose. A provider that does not report a token kind
leaves NULL, never 0 — measured examples: DeepSeek reports cache *reads*
(``prompt_cache_hit_tokens``) but no cache *write* count, and Google reports
``cached_content_token_count`` on every response, where a 0 is a real "no hit"
rather than "not reported". Writing 0 for an absent field would collapse those
two states into one, which is the exact blindness this feature removes.

**No new RLS policy is needed, and that is deliberate — not an oversight.**
``llm_call_record`` already carries ``tenant_id``, had RLS enabled and the
``humetric_app`` grant issued in 020, and 022 rewrote its policy body to the
blank-GUC-safe ``NULLIF(current_setting('app.tenant_id', true), '')::bigint``
form. Row-level policies are evaluated per row, not per column, so adding
columns does not touch them. (If a future migration needs the policy shape, copy
it from 022 — 020's on-disk source still holds the bare
``current_setting(...)::bigint`` form that 022 replaced, so it is a broken
template.)

Revision ID: 023
Revises: 022
Create Date: 2026-09-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None

_TABLE = "llm_call_record"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("input_tokens", sa.Integer(), nullable=True))
    op.add_column(_TABLE, sa.Column("output_tokens", sa.Integer(), nullable=True))
    op.add_column(_TABLE, sa.Column("cache_read_tokens", sa.Integer(), nullable=True))
    op.add_column(_TABLE, sa.Column("cache_write_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, "cache_write_tokens")
    op.drop_column(_TABLE, "cache_read_tokens")
    op.drop_column(_TABLE, "output_tokens")
    op.drop_column(_TABLE, "input_tokens")
