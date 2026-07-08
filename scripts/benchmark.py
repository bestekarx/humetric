#!/usr/bin/env python3
"""HuMetric Bulk Benchmark — submit thousands of realistic signals and measure throughput.

Features:
  - Generates realistic Turkish entities + signals via humetric.generator
  - Creates entities in parallel batches via the HuMetric REST API
  - Submits signals at maximum async concurrency
  - Progress bar with live throughput (signals/sec)
  - Validate mode: poll entities and verify metric quality after processing
  - Summary report with entity counts, signal counts, timing, and metric stats

Usage:
    python scripts/benchmark.py submit \\
        --api-key hm_live_xxx --base-url http://localhost:8002 \\
        --isci 50 --bayi 50 --cari 30 --bs 20 \\
        --signals-per 25 --concurrency 200

    python scripts/benchmark.py validate \\
        --api-key hm_live_xxx --base-url http://localhost:8002 \\
        --entity-ids isci-001 isci-002 ...

    python scripts/benchmark.py report \\
        --api-key hm_live_xxx --base-url http://localhost:8002 \\
        --entity-ids isci-001 isci-002 ... --output report.json
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from argparse import ArgumentParser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

# Allow running as a script without installing the package.
_THIS_DIR = Path(__file__).resolve().parent.parent
if str(_THIS_DIR / "src") not in sys.path:
    sys.path.insert(0, str(_THIS_DIR / "src"))

from humetric.generator import Generator


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

@dataclass
class RunConfig:
    api_key: str
    base_url: str
    concurrency: int = 200
    batch_size: int = 100
    seed: int = 42

    entity_counts: dict[str, int] = field(default_factory=lambda: {
        "isci": 50, "bayi": 50, "cari": 30, "bolge_sorumlusu": 20,
    })
    signals_per_entity: dict[str, int] = field(default_factory=lambda: {
        "isci": 25, "bayi": 20, "cari": 20, "bolge_sorumlusu": 20,
    })

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }


# ---------------------------------------------------------------------------
# Entity creation
# ---------------------------------------------------------------------------

async def _create_entity(client: httpx.AsyncClient, cfg: RunConfig,
                         sem: asyncio.Semaphore, entity: dict[str, Any],
                         retries: int = 3) -> tuple[str, bool]:
    """Create a single entity. Returns (entity_id, is_new)."""
    eid = entity["id"]
    for attempt in range(retries):
        async with sem:
            try:
                resp = await client.post(
                    f"{cfg.base_url}/v1/entities",
                    json=entity,
                    headers=cfg.headers,
                    timeout=30,
                )
                if resp.status_code in (200, 201):
                    return eid, resp.status_code == 201
                if resp.status_code == 429:
                    await asyncio.sleep(2 ** attempt)
                    continue
                # Other error — log and return failure
                print(f"  ! Entity {eid}: HTTP {resp.status_code} — {resp.text[:120]}")
                return eid, False
            except httpx.RequestError as exc:
                if attempt < retries - 1:
                    await asyncio.sleep(1)
                    continue
                print(f"  ! Entity {eid}: {exc}")
                return eid, False
    return eid, False


async def create_entities(cfg: RunConfig,
                          entities: list[dict[str, Any]]) -> dict[str, int]:
    """Create all entities in parallel. Returns {entity_id: status_code}."""
    sem = asyncio.Semaphore(cfg.concurrency)
    new_count = 0
    ok_count = 0
    failures: dict[str, str] = {}

    limits = httpx.Limits(
        max_keepalive_connections=cfg.concurrency,
        max_connections=cfg.concurrency,
    )
    async with httpx.AsyncClient(limits=limits) as client:
        tasks = [
            _create_entity(client, cfg, sem, e) for e in entities
        ]
        # Process in batches so we can show progress
        total = len(tasks)
        complete = 0
        batch = 200
        results: list[tuple[str, bool]] = []
        for i in range(0, total, batch):
            chunk = tasks[i:i + batch]
            chunk_results = await asyncio.gather(*chunk)
            results.extend(chunk_results)
            for _, is_new in chunk_results:
                if is_new:
                    new_count += 1
                ok_count += 1
            complete += len(chunk_results)
            print(f"  Entities: {complete}/{total} created ({new_count} new)")

    print(f"  Entities done: {ok_count} OK, {new_count} new, {failures and len(failures) or 0} failed")
    return {"ok": ok_count, "new": new_count}


# ---------------------------------------------------------------------------
# Signal submission
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkResult:
    """Aggregated benchmark metrics."""
    total_signals: int = 0
    submitted: int = 0
    failed: int = 0
    start_time: float = 0.0
    end_time: float = 0.0

    entity_count: int = 0
    entity_types: dict[str, int] = field(default_factory=dict)

    @property
    def elapsed_sec(self) -> float:
        return self.end_time - self.start_time if self.end_time > 0 else 0

    @property
    def signals_per_second(self) -> float:
        return self.submitted / self.elapsed_sec if self.elapsed_sec > 0 else 0

    @property
    def success_rate(self) -> float:
        return self.submitted / self.total_signals * 100 if self.total_signals else 0

    def summary(self) -> str:
        lines = [
            "=" * 60,
            "  HuMetric Bulk Benchmark — Results",
            "=" * 60,
            f"  Entities created : {self.entity_count}",
            f"  Signals submitted : {self.submitted:,} / {self.total_signals:,}",
            f"  Failed            : {self.failed}",
            f"  Success rate      : {self.success_rate:.1f}%",
            f"  Elapsed           : {self.elapsed_sec:.1f}s",
            f"  Throughput        : {self.signals_per_second:.1f} signals/sec",
            "",
        ]
        if self.entity_types:
            lines.append("  By entity type:")
            for et, cnt in sorted(self.entity_types.items()):
                lines.append(f"    {et:20s} : {cnt:,}")
            lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)


async def _submit_signal(client: httpx.AsyncClient, cfg: RunConfig,
                         sem: asyncio.Semaphore, signal: dict[str, Any],
                         retries: int = 3) -> bool:
    """Submit one signal. Returns True on success."""
    for attempt in range(retries):
        async with sem:
            try:
                ext_id = signal.get("external_id", str(uuid.uuid4()))
                resp = await client.post(
                    f"{cfg.base_url}/v1/signals",
                    json=signal,
                    headers={
                        **cfg.headers,
                        "Idempotency-Key": ext_id,
                    },
                    timeout=30,
                )
                if resp.status_code in (200, 202):
                    return True
                if resp.status_code == 429:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return False
            except httpx.RequestError:
                if attempt < retries - 1:
                    await asyncio.sleep(1)
                    continue
                return False
    return False


async def submit_signals(cfg: RunConfig,
                         signals: list[dict[str, Any]]) -> BenchmarkResult:
    """Submit all signals at maximum concurrency, tracking throughput."""
    result = BenchmarkResult(
        total_signals=len(signals),
        start_time=time.monotonic(),
    )

    sem = asyncio.Semaphore(cfg.concurrency)
    limits = httpx.Limits(
        max_keepalive_connections=cfg.concurrency,
        max_connections=cfg.concurrency,
    )

    async with httpx.AsyncClient(limits=limits) as client:
        tasks = [_submit_signal(client, cfg, sem, s) for s in signals]
        total = len(tasks)

        # Process in chunks with progress reporting
        chunk = max(cfg.concurrency, 50)
        for i in range(0, total, chunk):
            batch_tasks = tasks[i:i + chunk]
            batch_start = time.monotonic()
            batch_results = await asyncio.gather(*batch_tasks)
            batch_elapsed = time.monotonic() - batch_start

            submitted = sum(1 for ok in batch_results if ok)
            result.submitted += submitted
            result.failed += len(batch_results) - submitted
            done = min(i + chunk, total)

            throughput = submitted / batch_elapsed if batch_elapsed > 0 else 0
            print(
                f"  [{done:>5d}/{total:>5d}] "
                f"{result.submitted:>5d} ok  "
                f"{throughput:>7.1f} sig/s"
                + (f"  ({result.failed} failed)" if result.failed else "")
            )

    result.end_time = time.monotonic()
    return result


# ---------------------------------------------------------------------------
# Validator — poll metrics after processing
# ---------------------------------------------------------------------------

async def _get_entity_metrics(client: httpx.AsyncClient, cfg: RunConfig,
                              sem: asyncio.Semaphore, entity_id: str,
                              retries: int = 3) -> dict[str, Any] | None:
    """Fetch metrics for a single entity."""
    for attempt in range(retries):
        async with sem:
            try:
                resp = await client.get(
                    f"{cfg.base_url}/v1/entities/{entity_id}/metrics",
                    headers=cfg.headers,
                    timeout=30,
                )
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 404:
                    return {"entity_id": entity_id, "metrics": [], "metric_count": 0}
                await asyncio.sleep(1.5)
            except httpx.RequestError:
                if attempt < retries - 1:
                    await asyncio.sleep(1.5)
                    continue
                return None
    return None


@dataclass
class ValidationReport:
    entities_checked: int = 0
    entities_with_metrics: int = 0
    entities_without_metrics: int = 0
    total_metric_values: int = 0
    metric_keys_seen: set[str] = field(default_factory=set)
    avg_confidence: float = 0.0
    metric_value_dist: dict[str, list[float]] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            "",
            "=" * 60,
            "  HuMetric Bulk Benchmark — Validation Report",
            "=" * 60,
            f"  Entities checked          : {self.entities_checked}",
            f"  Has metrics               : {self.entities_with_metrics}",
            f"  No metrics yet            : {self.entities_without_metrics}",
            f"  Total metric values       : {self.total_metric_values}",
            f"  Unique metric keys        : {len(self.metric_keys_seen)}",
            f"  Average confidence        : {self.avg_confidence:.3f}",
            "",
        ]
        if self.metric_value_dist:
            lines.append("  Metric value ranges:")
            for key, vals in sorted(self.metric_value_dist.items()):
                if not vals:
                    continue
                lines.append(
                    f"    {key:25s} : min={min(vals):+.2f}  "
                    f"max={max(vals):+.2f}  "
                    f"mean={sum(vals) / len(vals):+.3f}  "
                    f"n={len(vals)}"
                )
            lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)


async def validate_metrics(cfg: RunConfig,
                           entity_ids: list[str]) -> ValidationReport:
    """Fetch metrics for all entities and build a validation report."""
    report = ValidationReport()
    sem = asyncio.Semaphore(min(cfg.concurrency, 50))

    limits = httpx.Limits(max_connections=50)
    async with httpx.AsyncClient(limits=limits) as client:
        tasks = [_get_entity_metrics(client, cfg, sem, eid) for eid in entity_ids]
        total = len(tasks)

        chunk_size = 50
        for i in range(0, total, chunk_size):
            batch = tasks[i:i + chunk_size]
            batch_results = await asyncio.gather(*batch)

            for resp in batch_results:
                if resp is None:
                    continue
                report.entities_checked += 1
                metrics = resp.get("metrics", [])
                if not metrics:
                    report.entities_without_metrics += 1
                    continue
                report.entities_with_metrics += 1

                for m in metrics:
                    report.total_metric_values += 1
                    key = m.get("metric_key", "unknown")
                    val = m.get("value", 0.0)
                    conf = m.get("confidence") or m.get("effective_confidence") or 0
                    report.metric_keys_seen.add(key)
                    report.avg_confidence += conf
                    report.metric_value_dist.setdefault(key, []).append(val)

            done = min(i + chunk_size, total)
            print(f"  Validation: {done}/{total} entities checked...")

    if report.total_metric_values > 0:
        report.avg_confidence /= report.total_metric_values
    return report


# ---------------------------------------------------------------------------
# Report generation (JSON output)
# ---------------------------------------------------------------------------

async def generate_report(cfg: RunConfig, entity_ids: list[str],
                          output_path: str) -> None:
    """Fetch all entities with their metrics and write a JSON report."""
    sem = asyncio.Semaphore(min(cfg.concurrency, 50))
    limits = httpx.Limits(max_connections=50)

    full_data: list[dict[str, Any]] = []

    async with httpx.AsyncClient(limits=limits) as client:

        async def _fetch_entity(eid: str) -> dict[str, Any] | None:
            async with sem:
                try:
                    resp = await client.get(
                        f"{cfg.base_url}/v1/entities/{eid}",
                        headers=cfg.headers,
                        timeout=30,
                    )
                    if resp.status_code == 200:
                        return resp.json()
                except Exception:
                    pass
            return None

        tasks = [_fetch_entity(eid) for eid in entity_ids]
        for i in range(0, len(tasks), 50):
            batch = tasks[i:i + 50]
            results = await asyncio.gather(*batch)
            full_data.extend(r for r in results if r is not None)

    report = {
        "total_entities": len(full_data),
        "entities": full_data,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    Path(output_path).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n  Report written to: {output_path}")
    print(f"  Entities in report: {len(full_data)}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_counts(args: Any, prefix: str) -> dict[str, int]:
    """Parse --isci 50 --bayi 30 style args into a dict."""
    mapping = {"isci": "isci", "bayi": "bayi", "cari": "cari", "bs": "bolge_sorumlusu"}
    result: dict[str, int] = {}
    for cli_key, etype in mapping.items():
        val = getattr(args, cli_key, None)
        if val is not None and val >= 0:
            result[etype] = val
    # Set missing types to 0 if they were not requested
    for etype in ("isci", "bayi", "cari", "bolge_sorumlusu"):
        result.setdefault(etype, 0)
    return result


def _make_config(args: Any) -> RunConfig:
    return RunConfig(
        api_key=args.api_key,
        base_url=args.base_url.rstrip("/"),
        concurrency=args.concurrency,
        batch_size=args.batch_size,
        seed=args.seed,
        entity_counts=_parse_counts(args, "entity"),
        signals_per_entity={k: args.signals_per for k in ("isci", "bayi", "cari", "bolge_sorumlusu")},
    )


async def cmd_submit(args: Any) -> None:
    """Generate entities + signals, create entities, submit signals."""
    cfg = _make_config(args)

    # Generate
    print("\n  Generating entities and signals...")
    gen = Generator(seed=cfg.seed)
    entities, signals = gen.generate(
        entity_counts=cfg.entity_counts,
        signals_per_entity=cfg.signals_per_entity,
    )
    entity_types = {}
    for e in entities:
        entity_types[e["entity_type"]] = entity_types.get(e["entity_type"], 0) + 1

    print(f"  Generated: {len(entities)} entities, {len(signals):,} signals")

    # Create entities
    print("\n  Creating entities...")
    ent_result = await create_entities(cfg, entities)

    # Submit signals
    print(f"\n  Submitting {len(signals):,} signals (concurrency={cfg.concurrency})...")
    result = await submit_signals(cfg, signals)
    result.entity_count = ent_result.get("ok", len(entities))
    result.entity_types = entity_types

    print(result.summary())

    # Print some entity IDs for later validation
    entity_ids = [e["id"] for e in entities]
    print("  Entity IDs (first 30):")
    for eid in entity_ids[:30]:
        print(f"    {eid}")

    # Save entity list for convenience
    id_file = Path("benchmark_entity_ids.txt")
    id_file.write_text("\n".join(entity_ids))
    print(f"\n  All {len(entity_ids)} entity IDs saved to {id_file}")


async def cmd_validate(args: Any) -> None:
    """Validate metrics for the given entities."""
    if not args.entity_ids:
        # Try to read from file
        id_file = Path("benchmark_entity_ids.txt")
        if id_file.exists():
            args.entity_ids = id_file.read_text().strip().splitlines()
            print(f"  Read {len(args.entity_ids)} entity IDs from {id_file}")
        else:
            print("  No entity IDs provided. Use --entity-ids or run 'submit' first.")
            return

    cfg = RunConfig(
        api_key=args.api_key,
        base_url=args.base_url.rstrip("/"),
        concurrency=args.concurrency,
    )

    print(f"\n  Validating {len(args.entity_ids)} entities...")
    report = await validate_metrics(cfg, args.entity_ids)
    print(report.summary())


async def cmd_report(args: Any) -> None:
    """Generate detailed JSON report."""
    if not args.entity_ids:
        id_file = Path("benchmark_entity_ids.txt")
        if id_file.exists():
            args.entity_ids = id_file.read_text().strip().splitlines()
        else:
            print("  No entity IDs provided.")
            return

    cfg = RunConfig(
        api_key=args.api_key,
        base_url=args.base_url.rstrip("/"),
        concurrency=args.concurrency,
    )

    output = args.output or "benchmark_report.json"
    await generate_report(cfg, args.entity_ids, output)


# ---- CLI setup ----

def _add_common_args(parser: ArgumentParser) -> None:
    parser.add_argument("--api-key", required=True, help="HuMetric API key (Bearer token)")
    parser.add_argument("--base-url", default="http://localhost:8002", help="HuMetric API base URL")
    parser.add_argument("--concurrency", type=int, default=200, help="Max concurrent HTTP requests")


def _add_entity_count_args(parser: ArgumentParser) -> None:
    parser.add_argument("--isci", type=int, default=50, help="Number of field worker entities")
    parser.add_argument("--bayi", type=int, default=50, help="Number of tire dealer entities")
    parser.add_argument("--cari", type=int, default=30, help="Number of customer entities")
    parser.add_argument("--bs", type=int, default=20, help="Number of regional manager entities")
    parser.add_argument("--signals-per", type=int, default=25, help="Signals per entity")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--batch-size", type=int, default=100, help="Entity creation batch size")


def main() -> None:
    parser = ArgumentParser(
        description="HuMetric Bulk Benchmark — realistic signal generation and throughput measurement"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_submit = sub.add_parser("submit", help="Generate entities, create them, and submit signals")
    _add_common_args(p_submit)
    _add_entity_count_args(p_submit)

    p_validate = sub.add_parser("validate", help="Check metrics on processed entities")
    _add_common_args(p_validate)
    p_validate.add_argument("--entity-ids", nargs="*", default=[], help="Entity IDs to check")

    p_report = sub.add_parser("report", help="Generate a detailed JSON report of all entities")
    _add_common_args(p_report)
    p_report.add_argument("--entity-ids", nargs="*", default=[], help="Entity IDs to include")
    p_report.add_argument("--output", default="benchmark_report.json", help="Output JSON file")

    args = parser.parse_args()

    if args.command == "submit":
        asyncio.run(cmd_submit(args))
    elif args.command == "validate":
        asyncio.run(cmd_validate(args))
    elif args.command == "report":
        asyncio.run(cmd_report(args))


if __name__ == "__main__":
    main()
