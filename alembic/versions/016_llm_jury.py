"""016_llm_jury: multiple active LLM providers + jury strategy.

Until now a tenant had exactly one active provider (``tenant.llm_provider``).
The dashboard now lets a tenant activate several at once: the same request runs
on every active provider in parallel and a jury reconciles the answers.

  * ``llm_providers``     — active providers, list order is the tie-breaker.
  * ``llm_jury_strategy`` — best_of | field_merge | majority.

``llm_provider`` is kept and mirrored to ``llm_providers[0]`` so older clients
and any code still reading the singular field stay correct.

Revision note: the previous ``016`` (016_analysis_session) was deleted in
62ae280, but databases provisioned before that removal are still stamped with
the bare string "016". This revision is therefore identified as
``016_llm_jury`` rather than ``016`` — a database stamped with the old id fails
loudly ("Can't locate revision 016") instead of silently treating these columns
as already created. Reconcile such a database with ``alembic stamp 015`` before
upgrading.

Revision ID: 016_llm_jury
Revises: 015
Create Date: 2026-08-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "016_llm_jury"
down_revision = "015"
branch_labels = None
depends_on = None

VALID_STRATEGIES = ("best_of", "field_merge", "majority")


def upgrade() -> None:
    op.add_column(
        "tenant",
        sa.Column(
            "llm_providers",
            JSONB,
            nullable=False,
            server_default=sa.text("'[\"anthropic\"]'::jsonb"),
        ),
    )
    op.add_column(
        "tenant",
        sa.Column(
            "llm_jury_strategy",
            sa.String(32),
            nullable=False,
            server_default="best_of",
        ),
    )

    # Carry each tenant's existing single choice into the list so nobody's
    # active provider changes as a side effect of this migration.
    op.execute(
        """
        UPDATE tenant
           SET llm_providers = to_jsonb(ARRAY[llm_provider])
         WHERE llm_provider IS NOT NULL
           AND llm_provider <> ''
        """
    )

    op.create_check_constraint(
        "ck_tenant_llm_jury_strategy",
        "tenant",
        "llm_jury_strategy IN ('best_of', 'field_merge', 'majority')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tenant_llm_jury_strategy", "tenant", type_="check")
    op.drop_column("tenant", "llm_jury_strategy")
    op.drop_column("tenant", "llm_providers")
