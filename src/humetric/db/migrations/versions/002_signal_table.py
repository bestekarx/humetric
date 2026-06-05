"""002_signal_table — signal ve usage_record tablolari + RLS

Revision ID: 002
Create Date: 2026-06-04
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "signal",
        sa.Column("id", sa.String(100), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=True),
        sa.Column("entity_id", sa.String(100), sa.ForeignKey("entity.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("structured", sa.dialects.postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="'received'"),
        sa.Column("result", sa.dialects.postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "external_id", name="uq_signal_external_id"),
    )
    op.create_index("ix_signal_entity", "signal", ["entity_id"])
    op.create_index("ix_signal_tenant", "signal", ["tenant_id"])
    op.create_index("ix_signal_status", "signal", ["tenant_id", "status"])

    # RLS
    op.execute("ALTER TABLE signal ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE signal FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON signal "
        "USING (tenant_id = current_setting('app.tenant_id')::bigint) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id')::bigint)"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON signal TO humetric_app")
    op.execute("GRANT USAGE ON SCHEMA public TO humetric_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO humetric_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO humetric_app")


def downgrade():
    op.drop_table("signal")
