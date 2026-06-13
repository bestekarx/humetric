"""Parquet dataset exporters for the analytics lakehouse.

Three datasets per tenant:
  - entity_metric_snapshot: full daily state (no history in Postgres)
  - signal: incremental archive with result scrub for KVKK
  - metering_record: incremental daily usage counters

All sensitive metric keys are unconditionally excluded — consent cannot be
re-evaluated against append-only Parquet files after the fact.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from humetric import config
from humetric.analytics import require_analytics

if TYPE_CHECKING:
    from humetric.analytics.storage import ExportStorage

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Parquet schemas (explicit so empty batches stay schema-stable)
# ---------------------------------------------------------------------------

def _build_schemas() -> tuple:
    import pyarrow as pa

    metric_snapshot_schema = pa.schema([
        pa.field("id", pa.int64()),
        pa.field("entity_id", pa.string()),
        pa.field("tenant_id", pa.int64()),
        pa.field("metric_key", pa.string()),
        pa.field("value", pa.float64()),
        pa.field("confidence", pa.float64()),
        pa.field("source_count", pa.int64()),
        pa.field("last_updated", pa.timestamp("us", tz="UTC")),
        pa.field("signal_id", pa.string()),
    ])

    signal_schema = pa.schema([
        pa.field("id", pa.string()),
        pa.field("tenant_id", pa.int64()),
        pa.field("external_id", pa.string()),
        pa.field("entity_id", pa.string()),
        pa.field("entity_type", pa.string()),
        pa.field("text", pa.string()),
        pa.field("structured", pa.string()),   # JSON string
        pa.field("status", pa.string()),
        pa.field("result", pa.string()),        # JSON string, scrubbed
        pa.field("error", pa.string()),
        pa.field("pack_key", pa.string()),
        pa.field("pack_version", pa.int64()),
        pa.field("created_at", pa.timestamp("us", tz="UTC")),
        pa.field("processed_at", pa.timestamp("us", tz="UTC")),
    ])

    metering_schema = pa.schema([
        pa.field("id", pa.int64()),
        pa.field("tenant_id", pa.int64()),
        pa.field("date", pa.date32()),
        pa.field("signal_count", pa.int64()),
        pa.field("llm_token_count", pa.int64()),
        pa.field("embedding_count", pa.int64()),
        pa.field("created_at", pa.timestamp("us", tz="UTC")),
    ])

    return metric_snapshot_schema, signal_schema, metering_schema


# ---------------------------------------------------------------------------
# KVKK: sensitive key collection
# ---------------------------------------------------------------------------

def collect_sensitive_metric_keys(pack_definitions: list[dict]) -> set[str]:
    """Union of all sensitive metric keys across all pack definitions for a tenant.

    Consent-independent: we cannot honour revocations in append-only Parquet.
    """
    keys: set[str] = set(config.HASSAS_METRIC_KEYS)
    for d in pack_definitions:
        kvkk = d.get("kvkk") or {}
        keys |= set(kvkk.get("sensitive_metrics") or [])
        keys |= {m["key"] for m in d.get("metrics", []) if m.get("sensitive") and m.get("key")}
    return keys


# ---------------------------------------------------------------------------
# Manifest (watermarks) stored as JSON in the lakehouse
# ---------------------------------------------------------------------------

def _manifest_path(tenant_id: int) -> str:
    return f"tenant_id={tenant_id}/_manifest.json"


async def _read_manifest(storage: ExportStorage, tenant_id: int) -> dict:
    raw = await storage.get(_manifest_path(tenant_id))
    if raw is None:
        return {"version": 1}
    return json.loads(raw.decode())


async def _write_manifest(storage: ExportStorage, tenant_id: int, manifest: dict) -> None:
    await storage.put(_manifest_path(tenant_id), json.dumps(manifest, default=str).encode())


# ---------------------------------------------------------------------------
# Parquet write helper
# ---------------------------------------------------------------------------

async def _write_parquet(
    storage: ExportStorage,
    path: str,
    rows: list[dict],
    schema: object,
) -> int:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pylist(rows, schema=schema)
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd")
    await storage.put(path, sink.getvalue().to_pybytes())
    return len(rows)


# ---------------------------------------------------------------------------
# Exporters
# ---------------------------------------------------------------------------

async def export_metric_snapshot(
    db: AsyncSession,
    tenant_id: int,
    storage: ExportStorage,
    export_date: date,
    sensitive_keys: set[str],
) -> int:
    """Export full entity_metric state for tenant as a daily snapshot."""
    from humetric.db.models import EntityMetric

    require_analytics()
    _, _, _ = _build_schemas()  # lazy import check
    metric_schema, _, _ = _build_schemas()

    stmt = (
        select(EntityMetric)
        .where(
            EntityMetric.tenant_id == tenant_id,
            EntityMetric.metric_key.not_in(sensitive_keys) if sensitive_keys else text("true"),
        )
        .order_by(EntityMetric.id)
    )
    result = await db.execute(stmt)
    metrics = result.scalars().all()

    rows = []
    for m in metrics:
        if m.metric_key in sensitive_keys:
            continue  # second gate
        rows.append({
            "id": m.id,
            "entity_id": m.entity_id,
            "tenant_id": m.tenant_id,
            "metric_key": m.metric_key,
            "value": m.value,
            "confidence": m.confidence,
            "source_count": m.source_count,
            "last_updated": m.last_updated,
            "signal_id": m.signal_id,
            # trace_data intentionally excluded: may embed sensitive values
        })

    path = f"tenant_id={tenant_id}/entity_metric_snapshot/date={export_date}/part-00000.parquet"
    return await _write_parquet(storage, path, rows, metric_schema)


async def export_signals(
    db: AsyncSession,
    tenant_id: int,
    storage: ExportStorage,
    run_started_at: datetime,
    sensitive_keys: set[str],
    manifest: dict,
) -> tuple[int, datetime | None]:
    """Export signal rows incrementally. Returns (row_count, max_created_at)."""
    from humetric.db.models import Signal

    require_analytics()
    _, signal_schema, _ = _build_schemas()

    wm_str = manifest.get("signal", {}).get("watermark")
    watermark = datetime.fromisoformat(wm_str) if wm_str else None

    stmt = select(Signal).where(Signal.tenant_id == tenant_id)
    if watermark:
        stmt = stmt.where(Signal.created_at > watermark)
    stmt = stmt.where(Signal.created_at <= run_started_at)
    stmt = stmt.order_by(Signal.created_at, Signal.id)

    result = await db.execute(stmt)
    signals = result.scalars().all()

    if not signals:
        return 0, None

    # Group by date(created_at) → one file per date partition
    by_date: dict[str, list[dict]] = {}
    max_created_at: datetime | None = None

    for s in signals:
        created_at = s.created_at
        if max_created_at is None or created_at > max_created_at:
            max_created_at = created_at

        # Scrub sensitive metrics from result JSON
        result_data = dict(s.result) if s.result else {}
        if "metrics" in result_data and sensitive_keys:
            result_data["metrics"] = [
                m for m in (result_data["metrics"] or [])
                if m.get("metric_key") not in sensitive_keys
            ]

        row = {
            "id": s.id,
            "tenant_id": s.tenant_id,
            "external_id": s.external_id,
            "entity_id": s.entity_id,
            "entity_type": s.entity_type,
            "text": s.text,
            "structured": json.dumps(s.structured or {}),
            "status": s.status,
            "result": json.dumps(result_data),
            "error": s.error,
            "pack_key": s.pack_key,
            "pack_version": s.pack_version,
            "created_at": created_at,
            "processed_at": s.processed_at,
        }

        day = str(created_at.date())
        by_date.setdefault(day, []).append(row)

    # Use run date as the file name suffix so retries overwrite, not duplicate
    run_date_str = run_started_at.date().isoformat()
    total = 0
    for day_str, day_rows in by_date.items():
        path = f"tenant_id={tenant_id}/signal/date={day_str}/part-r{run_date_str}.parquet"
        total += await _write_parquet(storage, path, day_rows, signal_schema)

    return total, max_created_at


async def export_metering(
    db: AsyncSession,
    tenant_id: int,
    storage: ExportStorage,
    export_date: date,
    manifest: dict,
) -> int:
    """Export metering_record rows incrementally (complete days only)."""
    from humetric.db.models import MeteringRecord

    require_analytics()
    _, _, metering_schema = _build_schemas()

    wm_str = manifest.get("metering_record", {}).get("watermark")
    watermark_date: date | None = date.fromisoformat(wm_str) if wm_str else None

    stmt = select(MeteringRecord).where(
        MeteringRecord.tenant_id == tenant_id,
        MeteringRecord.date < export_date,
    )
    if watermark_date:
        stmt = stmt.where(MeteringRecord.date > watermark_date)
    stmt = stmt.order_by(MeteringRecord.date)

    result = await db.execute(stmt)
    records = result.scalars().all()

    if not records:
        return 0

    total = 0
    for rec in records:
        rows = [{
            "id": rec.id,
            "tenant_id": rec.tenant_id,
            "date": rec.date,
            "signal_count": rec.signal_count,
            "llm_token_count": rec.llm_token_count,
            "embedding_count": rec.embedding_count,
            "created_at": rec.created_at,
        }]
        path = (
            f"tenant_id={tenant_id}/metering_record"
            f"/date={rec.date}/part-00000.parquet"
        )
        total += await _write_parquet(storage, path, rows, metering_schema)

    return total


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def run_tenant_export(
    db: AsyncSession,
    tenant_id: int,
    export_date: date | None = None,
) -> dict[str, int]:
    """Run all three exporters for a tenant. Returns {dataset: row_count}.

    Must be called inside a session where app.tenant_id GUC is set and an
    explicit tenant_id filter is applied by each exporter (belt-and-suspenders).
    """
    from humetric.analytics.storage import get_export_storage
    from humetric.store import Store

    require_analytics()

    if export_date is None:
        export_date = datetime.now(timezone.utc).date()

    run_started_at = datetime.now(timezone.utc)

    storage = get_export_storage()

    # Collect sensitive keys from ALL packs (active + inactive)
    packs = await Store.list_packs(db, tenant_id)
    sensitive_keys = collect_sensitive_metric_keys(
        [p.definition for p in packs if p.definition]
    )
    if sensitive_keys:
        _log.info(
            "Tenant %d: excluding %d sensitive metric key(s) from export",
            tenant_id, len(sensitive_keys),
        )

    manifest = await _read_manifest(storage, tenant_id)

    # Run exporters
    snapshot_count = await export_metric_snapshot(
        db, tenant_id, storage, export_date, sensitive_keys
    )
    signal_count, max_created_at = await export_signals(
        db, tenant_id, storage, run_started_at, sensitive_keys, manifest
    )
    metering_count = await export_metering(
        db, tenant_id, storage, export_date, manifest
    )

    # Update manifest only after all three succeed
    manifest.setdefault("signal", {})
    manifest.setdefault("metering_record", {})
    manifest.setdefault("entity_metric_snapshot", {})

    if max_created_at is not None:
        manifest["signal"]["watermark"] = max_created_at.isoformat()
    manifest["signal"]["last_export_date"] = export_date.isoformat()
    manifest["signal"]["rows_last_run"] = signal_count

    manifest["metering_record"]["watermark"] = (
        (date.fromisoformat(
            manifest["metering_record"].get("watermark") or "1970-01-01"
        ).__str__())
        if metering_count == 0
        else str(export_date)
    )
    manifest["entity_metric_snapshot"]["last_export_date"] = export_date.isoformat()

    await _write_manifest(storage, tenant_id, manifest)

    stats = {
        "entity_metric_snapshot": snapshot_count,
        "signal": signal_count,
        "metering_record": metering_count,
    }
    _log.info("Tenant %d export complete: %s", tenant_id, stats)
    return stats
