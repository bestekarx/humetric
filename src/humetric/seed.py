"""Seed script — create a default tenant + API key.

Usage:
    python -m humetric.seed --tenant <kod> --ad "Company Name" [--api-key]
    humetric-seed --tenant <kod> --ad "Company Name" [--api-key]
"""

from __future__ import annotations

import argparse
import asyncio

from . import config
from .db.database import get_admin_async_session_factory
from .store import Store


async def _seed(kod: str, ad: str, api_key_label: str | None = None) -> None:
    """Create a default tenant + optional API key."""
    config.require_db()

    factory = get_admin_async_session_factory()
    async with factory() as db:
        existing = await Store.get_tenant_by_kod(db, kod)
        if existing:
            print(f"Tenant '{kod}' already exists (id={existing.id}).")
            tenant_id = existing.id
        else:
            tenant = await Store.create_tenant(db, {"kod": kod, "ad": ad or kod})
            tenant_id = tenant.id
            print(f"Tenant '{kod}' created (id={tenant_id}).")

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
            print(f"API key created (prefix={api_key.prefix}, id={api_key.id})")
            print(f"  Full key: {full_key}")


def main():
    parser = argparse.ArgumentParser(description="HuMetric seed tool")
    parser.add_argument("--tenant", help="Tenant code", required=True)
    parser.add_argument("--ad", help="Tenant name", default="")
    parser.add_argument("--api-key", help="API key label (optional)", default=None)
    args = parser.parse_args()
    asyncio.run(_seed(kod=args.tenant, ad=args.ad, api_key_label=args.api_key))


if __name__ == "__main__":
    main()
