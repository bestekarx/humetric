"""Analytics lakehouse export CLI.

Usage:
    python -m humetric.export --tenant CODE [--date YYYY-MM-DD] [--run-now]
    python -m humetric.export --all-tenants [--date YYYY-MM-DD] [--run-now]

Default action is --enqueue (adds a task to the worker queue). Use --run-now
to execute the export inline (e.g. for backfill or local development without
a running worker).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date

from . import config
from .db.database import get_admin_async_session_factory
from .store import Store

_log = logging.getLogger(__name__)


async def _enqueue(
    tenant_codes: list[str] | None,
    all_tenants: bool,
    export_date: str,
) -> None:
    config.require_db()
    factory = get_admin_async_session_factory()
    async with factory() as db:
        if all_tenants:
            tenants = await Store.list_active_tenants(db)
        else:
            tenants = []
            for code in (tenant_codes or []):
                t = await Store.get_tenant_by_code(db, code)
                if t is None:
                    print(f"Tenant not found: {code!r}")
                    continue
                tenants.append(t)

        for tenant in tenants:
            already = await Store.has_export_task_for_date(db, tenant.id, export_date)
            if already:
                print(f"[skip] tenant={tenant.code} date={export_date}: task already exists")
                continue
            await Store.create_lakehouse_export_task(db, tenant.id, export_date)
            print(f"[enqueued] tenant={tenant.code} (id={tenant.id}) date={export_date}")


async def _run_now(
    tenant_codes: list[str] | None,
    all_tenants: bool,
    export_date_str: str,
) -> None:
    config.require_db()

    try:
        from .analytics.export import run_tenant_export
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)

    export_date = date.fromisoformat(export_date_str)
    factory = get_admin_async_session_factory()

    async with factory() as db:
        if all_tenants:
            tenants = await Store.list_active_tenants(db)
        else:
            tenants = []
            for code in (tenant_codes or []):
                t = await Store.get_tenant_by_code(db, code)
                if t is None:
                    print(f"Tenant not found: {code!r}")
                    continue
                tenants.append(t)

        from sqlalchemy import text

        for tenant in tenants:
            print(f"Exporting tenant={tenant.code} (id={tenant.id}) date={export_date} ...")
            await db.execute(
                text("SELECT set_config('app.tenant_id', :t, false)"),
                {"t": str(tenant.id)},
            )
            try:
                stats = await run_tenant_export(db, tenant.id, export_date)
                print(f"  Done: {stats}")
            except Exception as exc:
                print(f"  ERROR: {exc}")
            finally:
                try:
                    await db.execute(text("SELECT set_config('app.tenant_id', '', false)"))
                except Exception:
                    pass


def main():
    parser = argparse.ArgumentParser(
        description="HuMetric analytics lakehouse export",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tenant", metavar="CODE", nargs="+", help="Tenant code(s)")
    group.add_argument("--all-tenants", action="store_true", help="Export all active tenants")

    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        metavar="YYYY-MM-DD",
        help="Export date (default: today)",
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--enqueue",
        action="store_true",
        default=True,
        help="Add export task to the worker queue (default)",
    )
    mode.add_argument(
        "--run-now",
        action="store_true",
        help="Run export inline (no worker needed; useful for backfill/dev)",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.run_now:
        asyncio.run(_run_now(args.tenant, args.all_tenants, args.date))
    else:
        asyncio.run(_enqueue(args.tenant, args.all_tenants, args.date))


if __name__ == "__main__":
    main()
