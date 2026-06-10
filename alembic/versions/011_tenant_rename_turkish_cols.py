"""011: Rename tenant table Turkish columns to English.

kod → code, ad → name, durum → status,
kota_sinyal_aylik → monthly_signal_quota, kota_entity → entity_quota.

Revision ID: 011
Revises: 010
Create Date: 2026-06-10
"""
from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("tenant", "kod", new_column_name="code")
    op.alter_column("tenant", "ad", new_column_name="name")
    op.alter_column("tenant", "durum", new_column_name="status")
    op.alter_column("tenant", "kota_sinyal_aylik", new_column_name="monthly_signal_quota")
    op.alter_column("tenant", "kota_entity", new_column_name="entity_quota")


def downgrade():
    op.alter_column("tenant", "code", new_column_name="kod")
    op.alter_column("tenant", "name", new_column_name="ad")
    op.alter_column("tenant", "status", new_column_name="durum")
    op.alter_column("tenant", "monthly_signal_quota", new_column_name="kota_sinyal_aylik")
    op.alter_column("tenant", "entity_quota", new_column_name="kota_entity")
