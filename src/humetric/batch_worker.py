"""Batch worker — drains the signal_process queue via the Anthropic Message
Batches API (50% cost) instead of synchronous per-signal calls.

Intended for **backfill**: run as a one-shot job that drains whatever is
queued, then exits::

    python -m humetric.batch_worker

Because the extractor must run before the curator, batching is two-phase:

  Phase A  one Haiku extraction request per signal (batched).
  Phase B  per signal: cold-start (no history) → local fast-path, no LLM;
           otherwise one Sonnet curation request (batched).

Then results are written and tasks completed, reusing the real-time worker's
``_persist_signal_result``. Tasks left in 'processing' by a crashed run are
reclaimed on the next start.

Caveat: within a single batch, multiple signals for the *same* entity all see
the pre-batch snapshot as "no history", so all take the fast-path and
``upsert_metric`` is last-write-wins (no cross-signal reconciliation). This is
acceptable for a cold load; for per-signal reconciliation use the real-time
worker.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import config
from .agents import base, curator, extractor
from .agents.versioning import hash_prompt, hash_schema, hash_text
from .schema import CurationResult, ExtractionResult
from .store import Store
from .worker import _persist_signal_result, handle_failure

_log = logging.getLogger(__name__)


async def _set_tenant(db: AsyncSession, tenant_id: int) -> None:
    await db.execute(
        text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tenant_id)}
    )


def _result_message(res):
    """Return (message, error) for a batch result item's ``.result``."""
    if res is None:
        return None, "no batch result"
    if getattr(res, "type", None) != "succeeded":
        return None, f"batch result: {getattr(res, 'type', 'unknown')}"
    return res.message, None


async def _prepare_task(db: AsyncSession, task) -> dict | None:
    """Load the entity and build extraction inputs for a claimed task.

    Returns a per-signal context dict, or None if the task can't be processed
    (in which case it is failed permanently).
    """
    await _set_tenant(db, task.tenant_id)
    payload = task.payload
    entity_id = payload.get("entity_id")
    signal_text_in = payload.get("text", "")
    pack_def = payload.get("pack_definition", {})

    entity = await Store.get_entity(db, entity_id, task.tenant_id)
    if not entity:
        await Store.fail_task_permanently(db, task.id, f"Entity not found: {entity_id}")
        return None

    llm_key = await base.get_tenant_llm_key(task.tenant_id, db)

    signal_text = signal_text_in or json.dumps(
        payload.get("structured", {}), sort_keys=True, ensure_ascii=False
    )
    input_hash = hash_text(signal_text)
    signal = await Store.get_signal(db, task.signal_id, task.tenant_id)
    if signal:
        signal.input_hash = input_hash
        db.add(signal)
        await db.commit()

    ctx = entity.free_text or ""
    pack_extraction_prompt = (pack_def.get("prompts", {}) or {}).get("extraction")
    pack_metrics = pack_def.get("metrics", []) or []
    sys_prompt, user_prompt = extractor.build_extract_inputs(
        signal_text_in, ctx, pack_extraction_prompt, pack_metrics,
    )

    return {
        "task": task,
        "entity": entity,
        "entity_id": entity_id,
        "pack_def": pack_def,
        "ctx": ctx,
        "input_hash": input_hash,
        "llm_key": llm_key,
        "extract_system": sys_prompt,
        "extract_user": user_prompt,
        "extract_meta": {
            "model": config.AGENT_MODEL,
            "prompt_hash": hash_prompt(sys_prompt),
            "schema_hash": hash_schema(ExtractionResult),
        },
        "curator_meta": {},
        "error": None,
    }


async def run_batch_once(db: AsyncSession) -> int:
    """Claim one block of queued signal_process tasks and process via batch.

    Returns the number of tasks claimed (0 means the queue is empty).
    """
    tasks = await Store.get_next_task(
        db, batch_size=config.BATCH_SUBMIT_SIZE, task_types=["signal_process"],
    )
    if not tasks:
        return 0
    _log.info("Claimed %d signal task(s) for batch", len(tasks))

    contexts: list[dict] = []
    for t in tasks:
        c = await _prepare_task(db, t)
        if c is not None:
            contexts.append(c)
    if not contexts:
        return len(tasks)

    usage_by_tenant: dict[int, list] = defaultdict(list)

    # ── Phase A: extraction batch (grouped by LLM key) ──────────────────
    by_key: dict[str, list[dict]] = defaultdict(list)
    for c in contexts:
        by_key[c["llm_key"]].append(c)

    for key, group in by_key.items():
        requests = [
            base.build_batch_request(
                custom_id=str(c["task"].id),
                model=config.AGENT_MODEL,
                system=c["extract_system"],
                user=c["extract_user"],
                schema=ExtractionResult,
                tool_name="extract_metrics",
                tool_description="Extract metrics from the signal text",
            )
            for c in group
        ]
        results = await base.submit_and_await_batch(requests, api_key=key)
        for c in group:
            msg, err = _result_message(results.get(str(c["task"].id)))
            if err:
                c["error"] = f"extraction {err}"
                continue
            c["extracted"] = base.parse_batch_result(msg, ExtractionResult, "extract_metrics").metrics
            usage_by_tenant[c["task"].tenant_id].append(msg)

    # ── Phase B: cold-start fast-path vs curation ───────────────────────
    to_curate: list[dict] = []
    for c in contexts:
        if c["error"]:
            continue
        await _set_tenant(db, c["task"].tenant_id)
        existing = await Store.get_entity_metrics(db, c["entity_id"], c["task"].tenant_id)
        c["existing_metrics"] = existing
        if not existing:
            # Cold-start fast-path — no LLM call.
            c["final_metrics"] = curator.finalize_first_observation(c["extracted"], c["pack_def"])
        elif not c["extracted"]:
            c["final_metrics"] = []
        else:
            to_curate.append(c)

    by_key2: dict[str, list[dict]] = defaultdict(list)
    for c in to_curate:
        by_key2[c["llm_key"]].append(c)

    for key, group in by_key2.items():
        requests = []
        for c in group:
            sys_prompt, user_prompt = curator.build_curate_inputs(
                c["extracted"], c["existing_metrics"], c["ctx"], c["pack_def"],
            )
            c["curator_meta"] = {
                "model": config.CURATOR_MODEL,
                "prompt_hash": hash_prompt(sys_prompt),
                "schema_hash": hash_schema(CurationResult),
            }
            requests.append(
                base.build_batch_request(
                    custom_id=str(c["task"].id),
                    model=config.CURATOR_MODEL,
                    system=sys_prompt,
                    user=user_prompt,
                    schema=CurationResult,
                    tool_name="curate_metrics",
                    tool_description="Validate the extracted metrics and determine final values",
                )
            )
        results = await base.submit_and_await_batch(requests, api_key=key)
        for c in group:
            msg, err = _result_message(results.get(str(c["task"].id)))
            if err:
                c["error"] = f"curation {err}"
                continue
            result = base.parse_batch_result(msg, CurationResult, "curate_metrics")
            c["final_metrics"] = curator.finalize_curation(
                result, c["extracted"], c["existing_metrics"], c["pack_def"],
            )
            usage_by_tenant[c["task"].tenant_id].append(msg)

    # ── Write phase ─────────────────────────────────────────────────────
    for c in contexts:
        t = c["task"]
        await _set_tenant(db, t.tenant_id)
        if c["error"]:
            await handle_failure(db, t, RuntimeError(c["error"]))
            continue
        try:
            await _persist_signal_result(
                db, t, c["entity"], c["extracted"], c["final_metrics"],
                c["extract_meta"], c["curator_meta"], c["existing_metrics"],
                c["pack_def"], c["input_hash"],
            )
            await Store.complete_task(db, t.id)
        except Exception as exc:
            _log.exception("Batch write failed for task %d: %s", t.id, exc)
            try:
                await db.rollback()
                await _set_tenant(db, t.tenant_id)
            except Exception:
                pass
            await handle_failure(db, t, exc)

    # ── Token accounting (per tenant) ───────────────────────────────────
    for tenant_id, msgs in usage_by_tenant.items():
        await base.record_batch_usage(msgs, tenant_id)

    return len(tasks)


async def main() -> None:
    config.require_keys()
    _log.info(
        "Batch worker starting. Submit size: %d, poll: %.0fs, reclaim: %.0fs",
        config.BATCH_SUBMIT_SIZE, config.BATCH_POLL_INTERVAL_S, config.BATCH_RECLAIM_S,
    )

    from .db.database import get_admin_async_session_factory

    factory = get_admin_async_session_factory()

    async with factory() as db:
        reclaimed = await Store.reclaim_stale_tasks(db, config.BATCH_RECLAIM_S)
        if reclaimed:
            _log.info("Reclaimed %d stale 'processing' task(s)", reclaimed)

    total = 0
    while True:
        async with factory() as db:
            n = await run_batch_once(db)
        if n == 0:
            break
        total += n

    _log.info("Batch backfill complete. Claimed %d task(s) total.", total)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(main())
