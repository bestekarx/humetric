"""009: Entity tablosu composite primary key (tenant_id, id).

Once entity.id tek basina primary key idi; bu yuzden iki farkli tenant
ayni entity id'sini (or. 'isci_ahmet') kullanamiyordu — ikinci ekleme
duplicate key (entity_pkey) ihlali veriyordu. Cok-kiracili izolasyon
icin PK (tenant_id, id) yapilir ve child tablolardaki (entity_metric,
signal, consent) entity FK'leri composite hale getirilir.

Revision ID: 009
Revises: 008
Create Date: 2026-06-05
"""

from __future__ import annotations

from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- child FK'leri once dusur (entity PK degisecek) ---
    op.drop_constraint("entity_metric_entity_id_fkey", "entity_metric", type_="foreignkey")
    op.drop_constraint("signal_entity_id_fkey", "signal", type_="foreignkey")
    op.drop_constraint("consent_entity_id_fkey", "consent", type_="foreignkey")

    # --- entity: tekil PK -> composite (tenant_id, id) ---
    op.drop_constraint("uq_entity_tenant_id", "entity", type_="unique")
    op.drop_constraint("entity_pkey", "entity", type_="primary")
    op.create_primary_key("entity_pkey", "entity", ["tenant_id", "id"])

    # --- entity_metric: unique key'e tenant_id ekle, composite FK kur ---
    op.drop_constraint("uq_entity_metric_key", "entity_metric", type_="unique")
    op.create_unique_constraint(
        "uq_entity_metric_key", "entity_metric", ["tenant_id", "entity_id", "metric_key"]
    )
    op.create_foreign_key(
        "fk_entity_metric_entity",
        "entity_metric",
        "entity",
        ["tenant_id", "entity_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )

    # --- signal: composite FK ---
    op.create_foreign_key(
        "fk_signal_entity",
        "signal",
        "entity",
        ["tenant_id", "entity_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )

    # --- consent: composite FK ---
    op.create_foreign_key(
        "fk_consent_entity",
        "consent",
        "entity",
        ["tenant_id", "entity_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_consent_entity", "consent", type_="foreignkey")
    op.drop_constraint("fk_signal_entity", "signal", type_="foreignkey")
    op.drop_constraint("fk_entity_metric_entity", "entity_metric", type_="foreignkey")

    op.drop_constraint("uq_entity_metric_key", "entity_metric", type_="unique")
    op.create_unique_constraint(
        "uq_entity_metric_key", "entity_metric", ["entity_id", "metric_key"]
    )

    op.drop_constraint("entity_pkey", "entity", type_="primary")
    op.create_primary_key("entity_pkey", "entity", ["id"])
    op.create_unique_constraint("uq_entity_tenant_id", "entity", ["tenant_id", "id"])

    op.create_foreign_key(
        "entity_metric_entity_id_fkey", "entity_metric", "entity",
        ["entity_id"], ["id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "signal_entity_id_fkey", "signal", "entity",
        ["entity_id"], ["id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "consent_entity_id_fkey", "consent", "entity",
        ["entity_id"], ["id"], ondelete="CASCADE",
    )
