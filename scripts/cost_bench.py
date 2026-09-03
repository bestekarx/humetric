#!/usr/bin/env python3
"""HuMetric Cost Bench — measure what one signal actually costs us to process.

Answers, with measured numbers rather than guesses:

  * How much CPU-seconds and peak RAM does processing N signals burn, split
    between the pipeline process and the PostgreSQL backends it drives?
  * How many bytes of durable storage does one signal leave behind, per table?
  * How many LLM prompt/completion tokens does one signal consume (the
    customer's own cost under BYOK), and how many embedding calls?

It runs the real ``worker.process_one_task`` pipeline in-process against the
real database, so the extract -> merge -> write metrics -> history -> re-embed
path is exactly the production one. Only the two paid outbound calls can be
swapped for deterministic mocks (``--llm mock`` / ``--embed mock``) so the
infrastructure cost can be measured without spending money on providers.

Typical use — two runs, one for each cost side:

    # 1. Infra cost: 1000 signals, no provider spend.
    python scripts/cost_bench.py run --signals 1000 --entities 50 \
        --llm mock --embed mock --out /tmp/infra.json

    # 2. Token cost: small real sample, extrapolated by the report.
    python scripts/cost_bench.py run --signals 20 --entities 5 \
        --llm real --embed real --out /tmp/tokens.json

    # 3. Turn both into a price sheet.
    python scripts/cost_bench.py report --infra /tmp/infra.json \
        --tokens /tmp/tokens.json --prices scripts/cost_prices.json

    # 4. Drop everything the bench wrote.
    python scripts/cost_bench.py cleanup

Caveats it is honest about:
  * The pipeline runs in one process, not the API + worker split, so the HTTP
    ingest leg is measured separately (``ingest`` phase) rather than through
    uvicorn. Ingest is two INSERTs; the delta is small but it is not zero.
  * PostgreSQL RSS is summed over the backends this run opened. Shared buffers
    are counted once per backend by the OS, so peak RAM is an upper bound.
  * With ``--llm mock`` wall-clock throughput is CPU-bound and unrealistically
    high; production throughput is provider-latency-bound. Use ``--llm-latency-ms``
    to model that, and read CPU-seconds (not signals/sec) as the cost driver.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

BENCH_TENANT_CODE = "costbench"

# Tables the pipeline writes to, in the order the report prints them.
MEASURED_TABLES = [
    "entity",
    "entity_metric",
    "entity_metric_history",
    "signal",
    "task",
    "llm_call_record",
    "metering_record",
    "usage_record",
    "audit_log",
]


# ---------------------------------------------------------------------------
# Signal corpus — deterministic Turkish field-service text at a target length
# ---------------------------------------------------------------------------

_OPENERS = [
    "Sabah servisine {t} dakika gecikmeli katıldı",
    "Randevu saatinde sahada hazırdı",
    "Müşteri adresine planlanandan {t} dakika önce ulaştı",
    "Vardiya başlangıcında ekip toplantısına katılmadı",
    "İş emrini sisteme zamanında işledi",
]
_BODY = [
    "arıza tespiti sırasında multimetre kullanımı düzgündü ve ölçüm sonuçlarını fotoğrafladı",
    "kablo kanalını açarken duvara hasar verdi, sonrasında tamir etmeden ayrıldı",
    "müşteriye yapılan işlemi adım adım anlattı, sorulara sabırla cevap verdi",
    "kullanılan malzemeyi eksiksiz raporladı, hurda parçaları araca topladı",
    "ikinci kez aynı arıza için çağrıldı, ilk müdahalede kök nedeni bulamamıştı",
    "iş bitiminde alanı süpürdü ve müşteri onay formunu imzalattı",
    "ekip arkadaşına yük merdiveni taşımada yardım etti",
    "güvenlik ekipmanını (baret, eldiven) takmadan çatıya çıktı",
    "yedek parça talebini depoya vaktinde iletmediği için iş ertesi güne kaldı",
    "müşterinin ek talebini iş emrine ekletmeden ücretsiz yaptı",
]
_CLOSERS = [
    "Müşteri memnuniyet anketinde {s}/5 puan verdi.",
    "Bölge sorumlusu notunda işin tekrar kontrol edilmesini istedi.",
    "Çağrı {d} dakikada kapatıldı.",
    "Ek not bulunmuyor.",
]


def make_signal_text(rnd: random.Random, target_chars: int) -> str:
    """Build one realistic signal of roughly ``target_chars`` characters."""
    parts = [
        rnd.choice(_OPENERS).format(t=rnd.randint(5, 45)),
        rnd.choice(_BODY),
    ]
    while sum(len(p) for p in parts) + len(parts) < target_chars:
        parts.append(rnd.choice(_BODY))
    parts.append(
        rnd.choice(_CLOSERS).format(s=rnd.randint(1, 5), d=rnd.randint(20, 180))
    )
    return ". ".join(parts)


# ---------------------------------------------------------------------------
# Resource sampling
# ---------------------------------------------------------------------------


@dataclass
class Sample:
    ts: float
    self_rss: int
    pg_rss: int


class ResourceSampler:
    """Samples this process and the PostgreSQL backends this run is driving.

    Backend PIDs are re-read from ``pg_stat_activity`` on every tick because the
    connection pool opens them lazily; CPU is accumulated per PID as
    (last seen total) - (first seen total), so a backend that appears mid-run
    still contributes only the CPU it burned while we watched it.
    """

    def __init__(self, dsn_params: dict, interval: float = 0.25):
        import psutil

        self._psutil = psutil
        self._dsn = dsn_params
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._self_proc = psutil.Process(os.getpid())
        self._self_cpu0 = self._sum_cpu(self._self_proc)
        self._self_cpu1 = self._self_cpu0
        self._pg_cpu: dict[int, list[float]] = {}  # pid -> [first, last]
        self.samples: list[Sample] = []
        self.error: str | None = None

    @staticmethod
    def _sum_cpu(proc) -> float:
        t = proc.cpu_times()
        return t.user + t.system

    def _pg_backend_pids(self, conn) -> list[int]:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pid FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (self._dsn.get("dbname"),),
            )
            return [r[0] for r in cur.fetchall()]

    def _loop(self) -> None:
        import psycopg

        try:
            conn = psycopg.connect(autocommit=True, **self._dsn)
        except Exception as exc:  # sampling is best-effort, never fatal
            self.error = f"pg_stat_activity connect failed: {exc}"
            conn = None

        while not self._stop.is_set():
            try:
                self._self_cpu1 = self._sum_cpu(self._self_proc)
                self_rss = self._self_proc.memory_info().rss
                pg_rss = 0
                if conn is not None:
                    for pid in self._pg_backend_pids(conn):
                        try:
                            p = self._psutil.Process(pid)
                            cpu = self._sum_cpu(p)
                            pg_rss += p.memory_info().rss
                        except (self._psutil.NoSuchProcess, self._psutil.AccessDenied):
                            continue
                        slot = self._pg_cpu.get(pid)
                        if slot is None:
                            self._pg_cpu[pid] = [cpu, cpu]
                        else:
                            slot[1] = cpu
                self.samples.append(Sample(time.time(), self_rss, pg_rss))
            except Exception as exc:
                self.error = str(exc)
            self._stop.wait(self._interval)

        if conn is not None:
            conn.close()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> dict:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._self_cpu1 = max(self._self_cpu1, self._sum_cpu(self._self_proc))
        pg_cpu = sum(last - first for first, last in self._pg_cpu.values())
        return {
            "pipeline_cpu_s": round(self._self_cpu1 - self._self_cpu0, 3),
            "postgres_cpu_s": round(pg_cpu, 3),
            "pipeline_peak_rss_mb": round(
                max((s.self_rss for s in self.samples), default=0) / 1e6, 1
            ),
            "postgres_peak_rss_mb": round(
                max((s.pg_rss for s in self.samples), default=0) / 1e6, 1
            ),
            "postgres_backends_seen": len(self._pg_cpu),
            "samples": len(self.samples),
            "sampler_error": self.error,
        }


# ---------------------------------------------------------------------------
# Mocks for the two paid outbound calls
# ---------------------------------------------------------------------------


@dataclass
class MockLlmStats:
    calls: int = 0
    system_chars: int = 0
    user_chars: int = 0
    output_chars: int = 0


def install_llm_mock(
    pack_metrics: list[dict],
    stats: MockLlmStats,
    *,
    rnd: random.Random,
    latency_ms: float,
    review_rate: float,
    chars_per_token: float,
) -> None:
    """Replace the provider call with a deterministic in-process responder.

    Everything upstream of the network hop still runs for real: the prompt is
    assembled by ``extractor.build_extract_inputs``, the response is validated
    through the real ``ExtractionResult`` schema, ``call_meta`` is filled the
    way the providers fill it, and token usage is recorded through
    ``usage_service`` — so the DB write volume matches a real run.
    """
    from humetric.agents import extractor, multi_llm
    from humetric.agents.versioning import hash_prompt, hash_schema
    from humetric.schema import ExtractedMetric, ExtractionResult

    keys = [m["key"] for m in pack_metrics if m.get("key")]

    async def _mock(*, provider, model, api_key, system, user, schema, tenant_id=None,
                    call_meta=None, signal_id=None, pack_key=None, pack_version=None,
                    **_kw):
        stats.calls += 1
        stats.system_chars += len(system)
        stats.user_chars += len(user)

        if latency_ms:
            await asyncio.sleep(latency_ms / 1000.0)

        # Quote a real span out of the signal so the verbatim check in
        # worker._source_span_verified passes for most metrics and routes a
        # realistic slice to pending_review.
        body = user.split("<signal_text>", 1)[-1].split("</signal_text>", 1)[0].strip()
        words = body.split()
        metrics = []
        for key in keys:
            if rnd.random() < review_rate or len(words) < 8:
                span = "bu ifade sinyalde bulunmuyor"
            else:
                i = rnd.randrange(0, max(1, len(words) - 6))
                span = " ".join(words[i:i + 6])
            metrics.append(
                ExtractedMetric(
                    metric_key=key,
                    value=round(rnd.uniform(-1.0, 1.0), 3),
                    confidence=round(rnd.uniform(0.4, 0.95), 3),
                    reasoning=(
                        "Sinyalde bu metrik için doğrudan gözlem var; alıntılanan "
                        "ifade değerlendirmeye esas alındı."
                    ),
                    needs_review=rnd.random() < review_rate,
                    source_span=span,
                )
            )
        result = ExtractionResult(metrics=metrics)
        out_chars = len(result.model_dump_json())
        stats.output_chars += out_chars

        if call_meta is not None:
            call_meta["model"] = f"mock/{model}"
            call_meta["prompt_hash"] = hash_prompt(system)
            call_meta["schema_hash"] = hash_schema(schema)

        if tenant_id is not None:
            est_tokens = int((len(system) + len(user) + out_chars) / chars_per_token)
            from humetric.services.usage_service import record_llm_tokens
            await record_llm_tokens(
                tenant_id, est_tokens,
                signal_id=signal_id, pack_key=pack_key, pack_version=pack_version,
                provider=f"mock-{provider}", model=f"mock/{model}",
            )
        return result

    multi_llm.structured_call_multi = _mock
    extractor.structured_call_multi = _mock  # imported by name at module load


@dataclass
class MockEmbedStats:
    calls: int = 0
    chars: int = 0


def install_embed_mock(stats: MockEmbedStats, latency_ms: float) -> None:
    """Replace the embedding provider with a deterministic vector generator."""
    from humetric import config, embeddings

    class _MockProvider(embeddings.EmbeddingProvider):
        async def embed(self, texts: list[str]) -> list[list[float]]:
            stats.calls += len(texts)
            stats.chars += sum(len(t) for t in texts)
            if latency_ms:
                await asyncio.sleep(latency_ms / 1000.0)
            out = []
            for t in texts:
                r = random.Random(hash(t) & 0xFFFFFFFF)
                v = [r.uniform(-1, 1) for _ in range(self.dimensions)]
                norm = sum(x * x for x in v) ** 0.5 or 1.0
                out.append([x / norm for x in v])
            return out

    async def _get(tenant_id, db):
        return _MockProvider(dimensions=config.EMBED_DIM_VOYAGE)

    embeddings.get_tenant_embedding_provider = _get


# ---------------------------------------------------------------------------
# DB measurement helpers
# ---------------------------------------------------------------------------


async def tenant_byte_footprint(db, tenant_id: int) -> dict[str, int]:
    """Logical bytes this tenant's rows occupy, per table.

    ``pg_column_size(t.*)`` sums the on-disk width of each row including TOASTed
    JSONB, which is what actually grows with signal volume. Unlike
    ``pg_total_relation_size`` it is tenant-attributable and unaffected by other
    tenants writing to the same tables — the right basis for per-signal pricing.
    Index overhead is NOT included; the report adds a configurable multiplier.
    """
    from sqlalchemy import text

    out: dict[str, int] = {}
    for table in MEASURED_TABLES:
        try:
            row = await db.execute(
                text(f"SELECT COALESCE(SUM(pg_column_size(t.*)), 0), COUNT(*) "
                     f"FROM {table} t WHERE t.tenant_id = :tid"),
                {"tid": tenant_id},
            )
            size, count = row.one()
            out[table] = int(size)
            out[f"{table}__rows"] = int(count)
        except Exception:
            await db.rollback()
            out[table] = 0
            out[f"{table}__rows"] = 0
    return out


async def relation_sizes(db) -> dict[str, int]:
    """Physical size (heap + TOAST + indexes) of the measured tables."""
    from sqlalchemy import text

    rows = await db.execute(text(
        "SELECT c.relname, pg_total_relation_size(c.oid) "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r'"
    ))
    return {r[0]: int(r[1]) for r in rows if r[0] in MEASURED_TABLES}


# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------


async def ensure_tenant(db) -> int:
    from sqlalchemy import text

    row = await db.execute(
        text("SELECT id FROM tenant WHERE code = :c"), {"c": BENCH_TENANT_CODE}
    )
    existing = row.scalar_one_or_none()
    if existing:
        return int(existing)
    row = await db.execute(
        text("INSERT INTO tenant (code, name, tier, status) "
             "VALUES (:c, :n, 'enterprise', 'active') RETURNING id"),
        {"c": BENCH_TENANT_CODE, "n": "Cost Bench (local only)"},
    )
    tid = int(row.scalar_one())
    await db.commit()
    return tid


_PROVIDER_KEY_COLUMN = {
    "anthropic": "anthropic_key_encrypted",
    "openai": "openai_key_encrypted",
    "google": "google_ai_key_encrypted",
    "deepseek": "deepseek_key_encrypted",
}


async def configure_provider(db, tenant_id: int, provider: str, api_key: str) -> None:
    """Point the bench tenant at a BYO provider, exercising the real BYOK path.

    The key is encrypted into the tenant row exactly as ``/v1/tenant/keys``
    would store it, so ``get_tenant_llm_config`` resolves it the same way a
    real customer's does. It is read from an environment variable rather than
    an argv value so it never lands in shell history or ``ps`` output.
    """
    from sqlalchemy import text

    from humetric.store import encrypt_key

    column = _PROVIDER_KEY_COLUMN[provider]
    await db.execute(
        text(f"UPDATE tenant SET llm_provider = :p, {column} = :k WHERE id = :t"),
        {"p": provider, "k": encrypt_key(api_key), "t": tenant_id},
    )
    await db.commit()


async def ensure_pack(db, tenant_id: int, pack_path: Path) -> dict:
    import yaml
    from sqlalchemy import text

    definition = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
    pack_key = f"costbench-{definition['entity_type']}"
    await db.execute(
        text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tenant_id)}
    )
    await db.execute(
        text("INSERT INTO metric_pack (tenant_id, pack_key, version, definition, is_active) "
             "VALUES (:t, :k, 1, CAST(:d AS jsonb), true) "
             "ON CONFLICT (tenant_id, pack_key) DO UPDATE SET definition = EXCLUDED.definition"),
        {"t": tenant_id, "k": pack_key, "d": json.dumps(definition)},
    )
    await db.commit()
    return definition


async def seed_entities(db, tenant_id: int, entity_type: str, count: int, rnd) -> list[str]:
    from humetric.store import Store

    ids = []
    for i in range(count):
        eid = f"cb-{entity_type}-{i:05d}"
        await Store.upsert_entity(db, {
            "id": eid,
            "tenant_id": tenant_id,
            "entity_type": entity_type,
            "fields": {"lokasyon": rnd.choice(["İstanbul", "Ankara", "İzmir", "Bursa"]),
                       "statik_beceriler": "elektrik, tesisat"},
            "free_text": "Saha hizmet çalışanı, iki yıllık deneyim.",
        })
        ids.append(eid)
    return ids


async def cleanup(db) -> dict:
    from sqlalchemy import text

    row = await db.execute(
        text("SELECT id FROM tenant WHERE code = :c"), {"c": BENCH_TENANT_CODE}
    )
    tid = row.scalar_one_or_none()
    if tid is None:
        return {"deleted": False, "reason": "no bench tenant"}
    await db.execute(text("DELETE FROM tenant WHERE id = :t"), {"t": tid})
    await db.commit()
    return {"deleted": True, "tenant_id": int(tid)}


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


async def run_bench(args) -> dict:
    from humetric import config
    from humetric.db.database import get_admin_async_session_factory
    from humetric.store import Store
    from humetric.worker import process_one_task

    rnd = random.Random(args.seed)
    factory = get_admin_async_session_factory()

    async with factory() as db:
        tenant_id = await ensure_tenant(db)
        if args.provider:
            key = os.environ.get(args.provider_key_env, "")
            if not key:
                raise SystemExit(
                    f"--provider {args.provider} needs the key in ${args.provider_key_env}"
                )
            await configure_provider(db, tenant_id, args.provider, key)
        pack_def = await ensure_pack(db, tenant_id, Path(args.pack))
        entity_type = pack_def["entity_type"]
        pack_key = f"costbench-{entity_type}"
        entity_ids = await seed_entities(db, tenant_id, entity_type, args.entities, rnd)
        before_bytes = await tenant_byte_footprint(db, tenant_id)
        before_rel = await relation_sizes(db)

    pack_metrics = pack_def.get("metrics", []) or []
    llm_stats = MockLlmStats()
    embed_stats = MockEmbedStats()
    if args.llm == "mock":
        install_llm_mock(
            pack_metrics, llm_stats, rnd=rnd,
            latency_ms=args.llm_latency_ms, review_rate=args.review_rate,
            chars_per_token=args.chars_per_token,
        )
    if args.embed == "mock":
        install_embed_mock(embed_stats, args.embed_latency_ms)

    dsn = parse_dsn(config.DATABASE_URL)
    sampler = ResourceSampler(dsn, interval=args.sample_interval)

    # --- phase 1: ingest (what the API route writes per signal) -------------
    sampler.start()
    t_ingest0 = time.perf_counter()
    signal_ids: list[str] = []
    async with factory() as db:
        from sqlalchemy import text as _text
        await db.execute(_text("SELECT set_config('app.tenant_id', :t, false)"),
                         {"t": str(tenant_id)})
        for i in range(args.signals):
            sid = str(uuid.uuid4())
            eid = entity_ids[i % len(entity_ids)]
            body = make_signal_text(rnd, args.signal_chars)
            await Store.create_signal(db, {
                "id": sid, "tenant_id": tenant_id, "entity_id": eid,
                "entity_type": entity_type, "text": body, "structured": {},
                "external_id": None, "pack_key": pack_key, "pack_version": 1,
                "occurred_at": None,
            })
            await Store.create_task(db, {
                "tenant_id": tenant_id, "signal_id": sid,
                "task_type": "signal_process", "status": "queued",
                "payload": {
                    "entity_id": eid, "text": body, "entity_type": entity_type,
                    "structured": {}, "pack_definition": pack_def, "occurred_at": None,
                },
            })
            signal_ids.append(sid)
    ingest_s = time.perf_counter() - t_ingest0

    # --- phase 2: process (the worker pipeline) -----------------------------
    latencies: list[float] = []
    failures = 0
    lat_lock = asyncio.Lock()
    t_proc0 = time.perf_counter()

    # Claim the whole queue first, then shard it by entity. Store.upsert_metric
    # is a read-then-insert with no ON CONFLICT, so two coroutines holding
    # signals for the SAME entity race on uq_entity_metric_key. Production's
    # single worker processes its claimed batch sequentially and never hits it;
    # sharding by entity reproduces that safety while still exercising real
    # concurrency across entities, so the bench measures the pipeline rather
    # than a lock contention artefact.
    claimed = []
    async with factory() as cdb:
        while True:
            batch = await Store.get_next_task(
                cdb, batch_size=args.claim_batch, task_types=["signal_process"],
            )
            if not batch:
                break
            claimed.extend(t for t in batch if t.tenant_id == tenant_id)
            if len(claimed) >= args.signals:
                break
        # Detach so each shard can re-attach the row to its own session.
        shards: list[list[int]] = [[] for _ in range(args.concurrency)]
        for task in claimed:
            shards[hash(task.payload.get("entity_id", "")) % args.concurrency].append(task.id)

    async def drain(task_ids: list[int]) -> None:
        nonlocal failures
        if not task_ids:
            return
        from sqlalchemy import select as _select
        from humetric.db.models import Task as _Task
        async with factory() as wdb:
            for tid in task_ids:
                task = (await wdb.execute(
                    _select(_Task).where(_Task.id == tid)
                )).scalar_one()
                t0 = time.perf_counter()
                await process_one_task(wdb, task)
                dt = time.perf_counter() - t0
                await wdb.refresh(task)
                if task.status != "completed":
                    failures += 1
                async with lat_lock:
                    latencies.append(dt)

    await asyncio.gather(*(drain(s) for s in shards))
    process_s = time.perf_counter() - t_proc0
    resources = sampler.stop()

    # --- phase 3: measure ---------------------------------------------------
    async with factory() as db:
        from sqlalchemy import text as _text
        await db.execute(_text("SELECT set_config('app.tenant_id', :t, false)"),
                         {"t": str(tenant_id)})
        after_bytes = await tenant_byte_footprint(db, tenant_id)
        after_rel = await relation_sizes(db)
        row = await db.execute(_text(
            "SELECT COUNT(*), COALESCE(SUM(token_count), 0) FROM llm_call_record "
            "WHERE tenant_id = :t AND created_at >= :since"
        ), {"t": tenant_id, "since": datetime.fromtimestamp(t_ingest0 - time.perf_counter()
                                                            + time.time(), tz=timezone.utc)})
        llm_calls, llm_tokens = row.one()
        row = await db.execute(_text(
            "SELECT status, COUNT(*) FROM signal WHERE tenant_id = :t GROUP BY status"
        ), {"t": tenant_id})
        status_counts = {r[0]: int(r[1]) for r in row}
        row = await db.execute(_text(
            "SELECT COUNT(*) FROM entity_metric WHERE tenant_id = :t "
            "AND review_status = 'pending_review'"
        ), {"t": tenant_id})
        pending_review = int(row.scalar_one())

    n = args.signals
    byte_delta = {
        t: after_bytes.get(t, 0) - before_bytes.get(t, 0) for t in MEASURED_TABLES
    }
    row_delta = {
        t: after_bytes.get(f"{t}__rows", 0) - before_bytes.get(f"{t}__rows", 0)
        for t in MEASURED_TABLES
    }
    rel_delta = {
        t: after_rel.get(t, 0) - before_rel.get(t, 0) for t in set(after_rel) | set(before_rel)
    }
    total_bytes = sum(byte_delta.values())

    return {
        "meta": {
            "run_at": datetime.now(timezone.utc).isoformat(),
            "tenant_id": tenant_id,
            "pack": str(args.pack),
            "entity_type": entity_type,
            "metrics_in_pack": len(pack_metrics),
            "signals": n,
            "entities": args.entities,
            "signal_chars_target": args.signal_chars,
            "concurrency": args.concurrency,
            "llm_mode": args.llm,
            "embed_mode": args.embed,
            "llm_latency_ms": args.llm_latency_ms,
            "chars_per_token": args.chars_per_token,
        },
        "timing": {
            "ingest_s": round(ingest_s, 3),
            "process_s": round(process_s, 3),
            "total_s": round(ingest_s + process_s, 3),
            "signals_per_s": round(n / process_s, 2) if process_s else None,
            "per_signal_ms_p50": round(statistics.median(latencies) * 1000, 1) if latencies else None,
            "per_signal_ms_p95": round(
                sorted(latencies)[int(len(latencies) * 0.95) - 1] * 1000, 1
            ) if len(latencies) >= 20 else None,
            "processed": len(latencies),
            "failures": failures,
        },
        "resources": resources,
        "per_signal": {
            "pipeline_cpu_ms": round(resources["pipeline_cpu_s"] / n * 1000, 2),
            "postgres_cpu_ms": round(resources["postgres_cpu_s"] / n * 1000, 2),
            "total_cpu_ms": round(
                (resources["pipeline_cpu_s"] + resources["postgres_cpu_s"]) / n * 1000, 2
            ),
            "storage_bytes": round(total_bytes / n, 1),
            "llm_calls": round(llm_calls / n, 3),
            "llm_tokens": round(llm_tokens / n, 1),
            "embedding_calls": round(embed_stats.calls / n, 3) if args.embed == "mock" else None,
            "prompt_chars": round((llm_stats.system_chars + llm_stats.user_chars) / n, 1)
            if llm_stats.calls else None,
        },
        "storage_bytes_by_table": byte_delta,
        "rows_by_table": row_delta,
        "relation_size_delta": rel_delta,
        "llm": {
            "calls": int(llm_calls),
            "tokens": int(llm_tokens),
            "mock_stats": {
                "calls": llm_stats.calls,
                "system_chars": llm_stats.system_chars,
                "user_chars": llm_stats.user_chars,
                "output_chars": llm_stats.output_chars,
            } if args.llm == "mock" else None,
        },
        "embedding": {"calls": embed_stats.calls, "chars": embed_stats.chars}
        if args.embed == "mock" else None,
        "quality": {
            "signal_status_counts": status_counts,
            "metrics_pending_review": pending_review,
        },
    }


def parse_dsn(url: str) -> dict:
    """Turn a SQLAlchemy URL into psycopg connect kwargs for the sampler."""
    from urllib.parse import unquote, urlparse

    p = urlparse(url.replace("postgresql+psycopg", "postgresql"))
    return {
        "host": p.hostname or "localhost",
        "port": p.port or 5432,
        "user": unquote(p.username or ""),
        "password": unquote(p.password or ""),
        "dbname": (p.path or "/").lstrip("/"),
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

# Measured on 2026-09-03 with packs/saha-hizmet-isci.yaml (5 metrics), ~450
# character Turkish signals — the same corpus the offline counter uses. Kept
# here so the tokenizer proxy can be read against real provider invoices.
MEASURED_TOKEN_CALIBRATION = {
    "deepseek-chat": 1576,
    "gemini-2.5-flash": 1536,
}

DEFAULT_PRICES = {
    "_comment": "Verify every figure against your own invoices before quoting.",
    "server_eur_month": 25.0,
    "server_vcpu": 4,
    "server_ram_gb": 8,
    "storage_eur_gb_month": 0.10,
    "index_overhead_multiplier": 1.8,
    "backup_copies": 2,
    "retention_months": 12,
    "target_utilisation": 0.40,
    "embedding_usd_per_1m_tokens": 0.06,
    "embedding_chars_per_token": 4.0,
    "eur_per_usd": 0.92,
}


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def report(infra: dict, tokens: dict | None, prices: dict) -> str:
    m = infra["meta"]
    ps = infra["per_signal"]
    out: list[str] = []
    A = out.append

    n_ref = 1000
    cpu_s_per_1k = ps["total_cpu_ms"] * n_ref / 1000
    bytes_per_1k = ps["storage_bytes"] * n_ref

    vcpu_hours_avail = prices["server_vcpu"] * 730 * prices["target_utilisation"]
    eur_per_vcpu_hour = prices["server_eur_month"] / vcpu_hours_avail
    compute_eur_per_1k = cpu_s_per_1k / 3600 * eur_per_vcpu_hour

    stored_gb_per_1k = (
        bytes_per_1k
        * prices["index_overhead_multiplier"]
        * prices["backup_copies"]
        / 1024 ** 3
    )
    storage_eur_month_per_1k = stored_gb_per_1k * prices["storage_eur_gb_month"]
    storage_eur_lifetime_per_1k = storage_eur_month_per_1k * prices["retention_months"]

    embed_tokens_per_1k = 0.0
    if infra.get("embedding"):
        embed_tokens_per_1k = (
            infra["embedding"]["chars"] / m["signals"] * n_ref
            / prices["embedding_chars_per_token"]
        )
    embed_eur_per_1k = (
        embed_tokens_per_1k / 1e6
        * prices["embedding_usd_per_1m_tokens"]
        * prices["eur_per_usd"]
    )

    our_cost_per_1k = compute_eur_per_1k + storage_eur_lifetime_per_1k + embed_eur_per_1k

    A("=" * 74)
    A("HuMetric — ölçülen birim maliyet")
    A("=" * 74)
    A(f"Ölçüm: {m['signals']} sinyal, {m['entities']} entity, "
      f"{m['metrics_in_pack']} metrikli pack ({m['entity_type']}), "
      f"~{m['signal_chars_target']} karakter/sinyal")
    A(f"LLM: {m['llm_mode']}   Embedding: {m['embed_mode']}   "
      f"eşzamanlılık: {m['concurrency']}")
    A("")
    A("--- Sinyal başına ölçülen kaynak ---")
    A(f"  Pipeline CPU        : {ps['pipeline_cpu_ms']:>8.2f} ms")
    A(f"  PostgreSQL CPU      : {ps['postgres_cpu_ms']:>8.2f} ms")
    A(f"  Toplam CPU          : {ps['total_cpu_ms']:>8.2f} ms")
    A(f"  Kalıcı depolama     : {fmt_bytes(ps['storage_bytes']):>11}  (indekssiz, ham satır)")
    A(f"  LLM çağrısı         : {ps['llm_calls']:>8.3f}")
    if ps.get("prompt_chars"):
        A(f"  Prompt uzunluğu     : {ps['prompt_chars']:>8.0f} karakter")
    A("")
    A(f"  Pik RAM (pipeline)  : {infra['resources']['pipeline_peak_rss_mb']:>8.1f} MB")
    A(f"  Pik RAM (postgres)  : {infra['resources']['postgres_peak_rss_mb']:>8.1f} MB "
      f"({infra['resources']['postgres_backends_seen']} backend, shared buffers dahil → üst sınır)")
    A("")
    A("--- 1000 sinyal ---")
    A(f"  CPU                 : {cpu_s_per_1k:>8.1f} çekirdek-saniye "
      f"({cpu_s_per_1k / 3600:.4f} çekirdek-saat)")
    A(f"  Ham depolama        : {fmt_bytes(bytes_per_1k):>11}")
    A(f"  İndeks+yedek dahil  : {stored_gb_per_1k * 1024:>8.1f} MB "
      f"(x{prices['index_overhead_multiplier']} indeks, x{prices['backup_copies']} kopya)")
    A("")
    A("--- Bize maliyeti (1000 sinyal) ---")
    A(f"  Hesaplama           : €{compute_eur_per_1k:>8.4f}  "
      f"(€{eur_per_vcpu_hour:.4f}/vCPU-saat, %{prices['target_utilisation']*100:.0f} kullanım)")
    A(f"  {'Depolama (' + str(prices['retention_months']) + ' ay)':<20}: "
      f"€{storage_eur_lifetime_per_1k:>8.4f}")
    A(f"  Embedding           : €{embed_eur_per_1k:>8.4f}")
    A(f"  {'TOPLAM':<20}: €{our_cost_per_1k:>8.4f}")
    A("")
    A("  LLM token maliyeti bu toplamda YOK — BYOK'ta müşterinin faturasına gider.")
    A("")

    if infra["timing"]["failures"]:
        A(f"  !! {infra['timing']['failures']}/{m['signals']} sinyal HATA ile bitti — "
          f"aşağıdaki sayılar eksik. signal.error sütununa bak.")
        A("")

    A("--- Müşterinin LLM faturası (BYOK, 1000 sinyal) ---")
    src = tokens or infra
    tok_per_signal = src["per_signal"]["llm_tokens"]
    if tokens is None and m["llm_mode"] == "mock":
        A(f"  Tahmini token/sinyal: {tok_per_signal:.0f}  ← mock, "
          f"{m['chars_per_token']} karakter/token varsayımıyla. Gerçek ölçüm için:")
        A("    python scripts/cost_bench.py run --signals 20 --llm real --embed real")
    else:
        A(f"  Ölçülen token/sinyal: {tok_per_signal:.0f} "
          f"({src['meta']['signals']} sinyallik gerçek örneklem)")
    A(f"  1000 sinyal         : {tok_per_signal * n_ref:,.0f} token")
    A("")
    A("  $/1M token'ı kendi sağlayıcının güncel fiyat listesinden koy:")
    for price in (0.25, 1.0, 3.0, 15.0):
        A(f"    ${price:>5.2f}/1M → 1000 sinyal ≈ ${tok_per_signal * n_ref / 1e6 * price:,.2f}")
    A("")

    A("--- Fiyatlama tabanı ---")
    for mult, label in ((10, "10x"), (20, "20x"), (50, "50x")):
        A(f"  {label:>4} marj → 1000 sinyal €{our_cost_per_1k * mult:,.2f}")
    A("")
    A("  Not: bu maliyet tabanı fiyatın ALT sınırı, üst sınırı değil. Ölçekte")
    A("  altyapı maliyeti sinyal başına kuruşun altında kalıyor; fiyat, müşteriye")
    A("  sağladığın değere ve rakip alternatifin maliyetine göre belirlenir.")
    A("=" * 74)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Offline token accounting
# ---------------------------------------------------------------------------


def count_tokens(args) -> dict:
    """Count the tokens one signal costs, without calling (or paying) a provider.

    The prompt is built by the SAME ``extractor.build_extract_inputs`` the
    pipeline uses, so the split between fixed cost (system prompt + the pack's
    metric list, paid on every single signal) and variable cost (the signal
    text itself) is exact. Only the tokenizer is a stand-in: tiktoken's
    ``o200k_base`` rather than each provider's own. Expect ±10% against a
    provider invoice — enough to price a plan, not to reconcile a bill.

    The exact number lands in ``llm_call_record`` on its own once a funded key
    processes real traffic; ``run --llm real`` then reads it back.
    """
    import yaml
    import tiktoken

    from humetric.agents.extractor import build_extract_inputs
    from humetric.schema import ExtractedMetric, ExtractionResult

    enc = tiktoken.get_encoding("o200k_base")
    definition = yaml.safe_load(Path(args.pack).read_text(encoding="utf-8"))
    pack_metrics = definition.get("metrics", []) or []
    pack_prompt = (definition.get("prompts", {}) or {}).get("extraction")

    rnd = random.Random(args.seed)
    entity_ctx = "Saha hizmet çalışanı, iki yıllık deneyim. lokasyon: İstanbul"

    # Fixed leg: same prompt with an empty signal.
    sys_empty, user_empty = build_extract_inputs("", entity_ctx, pack_prompt, pack_metrics)
    fixed_tokens = len(enc.encode(sys_empty)) + len(enc.encode(user_empty))

    ins: list[int] = []
    for _ in range(args.samples):
        text = make_signal_text(rnd, args.signal_chars)
        system, user = build_extract_inputs(text, entity_ctx, pack_prompt, pack_metrics)
        ins.append(len(enc.encode(system)) + len(enc.encode(user)))

    # Output leg: a full response for every metric in the pack, with a
    # reasoning field of the length the prompt actually elicits.
    sample_out = ExtractionResult(metrics=[
        ExtractedMetric(
            metric_key=m["key"],
            value=0.42,
            confidence=0.75,
            reasoning="Sinyalde bu metriğe dair açık bir gözlem var; "
                      "alıntılanan ifade değerlendirmeye esas alındı.",
            needs_review=False,
            source_span="randevu saatinde sahada hazırdı",
        )
        for m in pack_metrics if m.get("key")
    ])
    out_tokens = len(enc.encode(sample_out.model_dump_json()))

    avg_in = statistics.mean(ins)
    return {
        "pack": str(args.pack),
        "metrics_in_pack": len(pack_metrics),
        "signal_chars": args.signal_chars,
        "samples": args.samples,
        "tokenizer": "tiktoken/o200k_base (proxy)",
        "input_tokens_avg": round(avg_in, 1),
        "input_tokens_fixed": fixed_tokens,
        "input_tokens_variable": round(avg_in - fixed_tokens, 1),
        "output_tokens": out_tokens,
        "total_tokens_per_signal": round(avg_in + out_tokens, 1),
    }


def token_report(t: dict) -> str:
    out: list[str] = []
    A = out.append
    A("=" * 74)
    A("Sinyal başına token — offline sayım (sağlayıcıya çağrı yok)")
    A("=" * 74)
    A(f"Pack: {Path(t['pack']).name}  ({t['metrics_in_pack']} metrik)   "
      f"sinyal ~{t['signal_chars']} karakter   n={t['samples']}")
    A(f"Tokenizer: {t['tokenizer']}")
    A("")
    A("  !! TÜRKÇE UYARISI — bu sayı ölçülmüş gerçek faturaların ALTINDA kalır.")
    A("  Aynı pack/sinyal ile ölçülen gerçek sağlayıcı raporları:")
    for name, real in MEASURED_TOKEN_CALIBRATION.items():
        A(f"     {name:<22} {real:>5} token  (x{real / t['total_tokens_per_signal']:.2f})")
    A("  Türkçe alt-kelime bölünmesi yüzünden tokenizer proxy'si iyimser çıkıyor;")
    A("  fiyatlarken aşağıdaki sayıyı ~x1.7 ile çarp ya da kendi sağlayıcında")
    A("  'run --llm real --provider ...' ile ölç.")
    A("")
    A(f"  Girdi (sabit: sistem promptu + pack metrik listesi) : "
      f"{t['input_tokens_fixed']:>7.0f} token")
    A(f"  Girdi (değişken: sinyal metni)                      : "
      f"{t['input_tokens_variable']:>7.0f} token")
    A(f"  Girdi toplam                                        : "
      f"{t['input_tokens_avg']:>7.0f} token")
    A(f"  Çıktı ({t['metrics_in_pack']} metriklik JSON)"
      f"{'':<25.25}: {t['output_tokens']:>7.0f} token")
    A(f"  {'SİNYAL BAŞINA TOPLAM':<52}: {t['total_tokens_per_signal']:>7.0f} token")
    A("")
    fixed_share = t['input_tokens_fixed'] / t['input_tokens_avg'] * 100
    A(f"  Girdinin %{fixed_share:.0f}'i sabit — her sinyalde yeniden ödenir.")
    A("  Prompt caching açılırsa bu kısım ~%90 ucuzlar; büyük pack'lerde")
    A("  tasarruf oranı daha da artar.")
    A("")
    A("--- 1000 sinyal, girdi/çıktı ayrı fiyatlanmış ---")
    A(f"  {'in $/1M':>9} {'out $/1M':>9} → {'1000 sinyal':>12}")
    for pin, pout in ((0.10, 0.40), (0.25, 1.25), (0.80, 4.00), (3.00, 15.00)):
        cost = (t["input_tokens_avg"] * pin + t["output_tokens"] * pout) / 1e6 * 1000
        A(f"  {pin:>9.2f} {pout:>9.2f} → {'$' + format(cost, ',.2f'):>12}")
    A("")
    A("  Sütunlara kendi sağlayıcının güncel liste fiyatını koy. BYOK'ta bu")
    A("  tutar müşterinin kendi faturasına gider, senin maliyetin değildir —")
    A("  ama fiyat konuşurken müşterinin toplam sahip olma maliyetidir.")
    A("=" * 74)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run a measured benchmark")
    r.add_argument("--signals", type=int, default=200)
    r.add_argument("--entities", type=int, default=20)
    r.add_argument("--pack", default=str(_ROOT / "packs" / "saha-hizmet-isci.yaml"))
    r.add_argument("--signal-chars", type=int, default=450,
                   help="target length of one generated signal")
    r.add_argument("--concurrency", type=int, default=4)
    r.add_argument("--claim-batch", type=int, default=5)
    r.add_argument("--llm", choices=["mock", "real"], default="mock")
    r.add_argument("--provider", choices=sorted(_PROVIDER_KEY_COLUMN),
                   help="configure the bench tenant's BYO provider before running")
    r.add_argument("--provider-key-env", default="HUMETRIC_BENCH_LLM_KEY",
                   help="env var holding the BYO key (never passed on argv)")
    r.add_argument("--embed", choices=["mock", "real"], default="mock")
    r.add_argument("--llm-latency-ms", type=float, default=0.0,
                   help="simulate provider latency (mock mode only)")
    r.add_argument("--embed-latency-ms", type=float, default=0.0)
    r.add_argument("--review-rate", type=float, default=0.15,
                   help="fraction of mock metrics routed to pending_review")
    r.add_argument("--chars-per-token", type=float, default=1.9,
                   help="mock-mode token estimate divisor; 1.9 measured for "
                        "Turkish against deepseek/gemini reported usage")
    r.add_argument("--sample-interval", type=float, default=0.25)
    r.add_argument("--seed", type=int, default=42)
    r.add_argument("--out", help="write the raw result JSON here")
    r.add_argument("--prices", help="price sheet JSON for the inline report")

    rep = sub.add_parser("report", help="turn saved runs into a cost/price sheet")
    rep.add_argument("--infra", required=True, help="JSON from a --llm mock run")
    rep.add_argument("--tokens", help="JSON from a --llm real run")
    rep.add_argument("--prices", help="price sheet JSON")

    tk = sub.add_parser("tokens", help="count tokens per signal offline (free, no API call)")
    tk.add_argument("--pack", default=str(_ROOT / "packs" / "saha-hizmet-isci.yaml"))
    tk.add_argument("--signal-chars", type=int, default=450)
    tk.add_argument("--samples", type=int, default=200)
    tk.add_argument("--seed", type=int, default=42)
    tk.add_argument("--out")

    sub.add_parser("cleanup", help="delete everything the bench wrote")
    sub.add_parser("prices", help="print the default price sheet as JSON")
    return p


def load_prices(path: str | None) -> dict:
    prices = dict(DEFAULT_PRICES)
    if path:
        prices.update(json.loads(Path(path).read_text()))
    return prices


def main() -> int:
    args = build_parser().parse_args()

    if args.cmd == "prices":
        print(json.dumps(DEFAULT_PRICES, indent=2))
        return 0

    if args.cmd == "report":
        infra = json.loads(Path(args.infra).read_text())
        tokens = json.loads(Path(args.tokens).read_text()) if args.tokens else None
        print(report(infra, tokens, load_prices(args.prices)))
        return 0

    if args.cmd == "tokens":
        t = count_tokens(args)
        if args.out:
            Path(args.out).write_text(json.dumps(t, indent=2, ensure_ascii=False))
        print(token_report(t))
        return 0

    if args.cmd == "cleanup":
        from humetric.db.database import get_admin_async_session_factory

        async def _go():
            factory = get_admin_async_session_factory()
            async with factory() as db:
                return await cleanup(db)

        print(json.dumps(asyncio.run(_go()), indent=2))
        return 0

    result = asyncio.run(run_bench(args))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"raw result → {args.out}", file=sys.stderr)
    print(report(result, None, load_prices(args.prices)))
    return 0


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.WARNING)
    raise SystemExit(main())
