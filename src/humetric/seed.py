"""Seed script — varsayilan tenant + API key olustur.

Kullanim:
    python -m humetric.seed --tenant <kod> --ad "Firma Adi" [--api-key]
    humetric-seed --tenant <kod> --ad "Firma Adi" [--api-key]

Saha seed.py pattern'inden uyarlanmistir.
"""

from __future__ import annotations

import argparse
import asyncio

from . import config
from .db.database import get_admin_async_session_factory
from .store import Store


async def _seed(kod: str, ad: str, api_key_label: str | None = None) -> None:
    """Varsayilan tenant + opsiyonel API key olustur."""
    config.require_db()

    factory = get_admin_async_session_factory()
    async with factory() as db:
        existing = await Store.get_tenant_by_kod(db, kod)
        if existing:
            print(f"Tenant '{kod}' zaten var (id={existing.id}).")
            tenant_id = existing.id
        else:
            tenant = await Store.create_tenant(db, {"kod": kod, "ad": ad or kod})
            tenant_id = tenant.id
            print(f"Tenant '{kod}' olusturuldu (id={tenant_id}).")

        if api_key_label:
            full_key, api_key = await Store.create_api_key(
                db,
                tenant_id=tenant_id,
                prefix="hm_live",
                label=api_key_label,
                scopes=[
                    "entities:write", "entities:read",
                    "signals:write", "signals:read",
                    "query",
                    "packs:read", "packs:admin",
                ],
            )
            print(f"API key olusturuldu (prefix={api_key.prefix}, id={api_key.id})")
            print(f"  Full key: {full_key}")


def main():
    parser = argparse.ArgumentParser(description="HuMetric seed aracı")
    parser.add_argument("--tenant", help="Tenant kodu", required=True)
    parser.add_argument("--ad", help="Tenant adı", default="")
    parser.add_argument("--api-key", help="API key etiketi (opsiyonel)", default=None)
    args = parser.parse_args()
    asyncio.run(_seed(kod=args.tenant, ad=args.ad, api_key_label=args.api_key))


if __name__ == "__main__":
    main()
