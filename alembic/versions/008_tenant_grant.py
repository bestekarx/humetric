"""008: Grant humetric_app access to tenant table.

tenant tablosu RLS uygulanmayan (tenant_id kendisi olan) tek
tablodur. 001 migration'inda GRANT atlanmis — humetric_app
kisitli runtime rolu tenant tablosunu okuyamaz veya yazamaz.

Bu migration sadece idempotent GRANT calistirir — alter table yok.

Revision ID: 008
Revises: 007
Create Date: 2026-06-05
"""

from __future__ import annotations

from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON tenant TO humetric_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON tenant FROM humetric_app")
