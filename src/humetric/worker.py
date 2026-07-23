"""Worker process — async task queue processing (Spec 024).

Pulls tasks from the queue with PostgreSQL SELECT FOR UPDATE SKIP LOCKED
and runs the extract → curate → write metrics → re-embed pipeline.
Exponential backoff retry, graceful shutdown (SIGTERM/SIGINT).
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from . import config, kvkk
from .agents import curator, extractor
from .store import Store, _build_embed_text_safe

_log = logging.getLogger(__name__)

_running = True


def _handle_shutdown(signum, frame):
    global _running
    _log.info("Received signal %s, shutting down gracefully...", signum)
    _running = False


signal.signal(signal.SIGTERM, _handle_shutdown)
signal.signal(signal.SIGINT, _handle_shutdown)


async def process_signal_task(db: AsyncSession, task) -> None:
    """Process a single signal task: extract → curate (or fast-path) → write → embed."""
    payload = task.payload
    entity_id = payload.get("entity_id")
    text = payload.get("text", "")
    pack_def = payload.get("pack_definition", {})

    entity = await Store.get_entity(db, entity_id, task.tenant_id)
    if not entity:
        raise ValueError(f"Entity not found: {entity_id}")

    from .agents.base import get_tenant_llm_config
    from .agents.versioning import hash_text

    llm_provider, llm_key = await get_tenant_llm_config(task.tenant_id, db)

    signal_text = text or json.dumps(payload.get("structured", {}), sort_keys=True, ensure_ascii=False)
    input_hash = hash_text(signal_text)
    signal = await Store.get_signal(db, task.signal_id, task.tenant_id)
    if signal:
        signal.input_hash = input_hash
        db.add(signal)
        await db.commit()

    ctx = entity.free_text or ""
    pack_extraction_prompt = (pack_def.get("prompts", {}) or {}).get("extraction")
    pack_metrics = pack_def.get("metrics", []) or []
    extract_meta: dict = {}
    extracted = await extractor.extract_metrics(
        text, ctx,
        pack_prompt=pack_extraction_prompt,
        pack_metrics=pack_metrics,
        tenant_id=task.tenant_id,
        api_key=llm_key,
        provider=llm_provider,
        call_meta=extract_meta,
    )
    existing_metrics = await Store.get_entity_metrics(db, entity_id, task.tenant_id)

    curator_meta: dict = {}
    if config.CURATOR_FAST_PATH_ENABLED and not existing_metrics:
        # Cold-start fast-path: with no history to reconcile, the Sonnet curator
        # is a near-deterministic pass-through. Finalize locally and skip the
        # LLM call. curator_meta stays empty → trace records curator_model=None
        # so the fast-path remains auditable.
        final_metrics = curator.finalize_first_observation(extracted, pack_def)
    else:
        final_metrics = await curator.curate_metrics(
            extracted, existing_metrics, ctx, pack_def,
            tenant_id=task.tenant_id,
            api_key=llm_key,
            provider=llm_provider,
            call_meta=curator_meta,
        )

    await _persist_signal_result(
        db, task, entity, extracted, final_metrics,
        extract_meta, curator_meta, existing_metrics, pack_def, input_hash,
    )


async def _persist_signal_result(
    db: AsyncSession,
    task,
    entity,
    extracted,
    final_metrics,
    extract_meta: dict,
    curator_meta: dict,
    existing_metrics,
    pack_def: dict,
    input_hash: str,
) -> None:
    """Write final metrics (KVKK-gated), re-embed the entity, and mark the
    signal completed. Shared by the real-time worker and the batch worker."""
    entity_id = entity.id
    skipped_sensitive: list[str] = []
    existing_by_key = {m.metric_key: m for m in existing_metrics}
    written_source_counts: dict[str, int] = {}

    for fm in final_metrics:
        prior = existing_by_key.get(fm.metric_key)
        source_count = (prior.source_count + 1) if prior else 1

        metric_def = _find_metric_def(pack_def, fm.metric_key)
        if metric_def and metric_def.get("sensitive"):
            consent_scope = metric_def.get("requires_consent_scope")
            if consent_scope:
                has_consent = await kvkk.check_consent_for_metric(
                    db, entity_id, consent_scope, task.tenant_id,
                )
                if not has_consent:
                    skipped_sensitive.append(fm.metric_key)
                    continue

        extracted_entries = [
            e.model_dump() for e in extracted if e.metric_key == fm.metric_key
        ]
        trace = {
            "extracted": extracted_entries,
            "extract_prompt_hash": extract_meta.get("prompt_hash"),
            "extract_schema_hash": extract_meta.get("schema_hash"),
            "extract_model": extract_meta.get("model"),
            "curator_prompt_hash": curator_meta.get("prompt_hash"),
            "curator_schema_hash": curator_meta.get("schema_hash"),
            "curator_model": curator_meta.get("model"),
            "needs_review": fm.needs_review,
        }
        await Store.upsert_metric(db, {
            "tenant_id": task.tenant_id,
            "entity_id": entity_id,
            "metric_key": fm.metric_key,
            "value": fm.value,
            "confidence": fm.confidence,
            "source_count": source_count,
            "signal_id": task.signal_id,
            "trace_data": trace,
            "input_hash": input_hash,
            "prompt_hash": extract_meta.get("prompt_hash"),
            "schema_hash": extract_meta.get("schema_hash"),
            "model": extract_meta.get("model"),
            "extraction_raw": {"extracted": extracted_entries},
            "review_status": "pending_review" if fm.needs_review else None,
        })
        written_source_counts[fm.metric_key] = source_count

    embed_text = _build_embed_text_safe(entity, existing_metrics, pack_def)
    try:
        await Store.update_entity_embedding(db, entity_id, task.tenant_id, embed_text)
    except Exception:
        _log.warning("Embedding failed for %s, setting pending flag", entity_id)
        await Store.set_embedding_pending(db, entity_id, True)
        await Store.create_re_embed_task(db, entity_id, task.tenant_id)

    result_metrics = [
        {
            "metric_key": fm.metric_key,
            "value": fm.value,
            "confidence": fm.confidence,
            "source_count": written_source_counts[fm.metric_key],
            "source_signal_id": task.signal_id,
            "needs_review": fm.needs_review,
        }
        for fm in final_metrics
        if fm.metric_key in written_source_counts
    ]

    await Store.update_signal_status(
        db, task.signal_id, task.tenant_id, "completed",
        result={"metrics": result_metrics},
    )


async def process_re_embed_task(db: AsyncSession, task) -> None:
    """Re-embed an entity whose embedding_pending flag is true."""
    payload = task.payload
    entity_id = payload.get("entity_id")

    entity = await Store.get_entity(db, entity_id, task.tenant_id)
    if not entity:
        return

    metrics = await Store.get_entity_metrics(db, entity_id, task.tenant_id)
    pack = await Store.get_active_pack_for_type(db, task.tenant_id, entity.entity_type)
    pack_def = pack.definition if pack else None

    embed_text = _build_embed_text_safe(entity, metrics, pack_def)
    await Store.update_entity_embedding(db, entity_id, task.tenant_id, embed_text)


async def process_lakehouse_export_task(db: AsyncSession, task) -> None:
    """Export tenant data to the analytics lakehouse (Parquet on local/S3)."""
    try:
        from .analytics.export import run_tenant_export
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc  # non-retryable: missing analytics deps

    from datetime import date as date_type

    export_date_str = task.payload.get("export_date")
    export_date = date_type.fromisoformat(export_date_str) if export_date_str else None

    stats = await run_tenant_export(db, task.tenant_id, export_date)
    _log.info("Lakehouse export task %d done: %s", task.id, stats)


async def handle_failure(db: AsyncSession, task, exc: Exception) -> None:
    """Decide whether to retry or permanently fail on error."""

    is_retryable = True
    status_code = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    if status_code and 400 <= status_code < 500:
        is_retryable = False
    elif isinstance(exc, ValueError):
        is_retryable = False

    if is_retryable and task.retry_count < task.max_retries:
        backoff = 2 ** task.retry_count
        next_retry = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        from datetime import timedelta
        next_retry = datetime.now(timezone.utc) + timedelta(seconds=backoff)
        _log.warning(
            "Task %d failed (attempt %d/%d), retrying in %ds: %s",
            task.id, task.retry_count + 1, task.max_retries, backoff, exc,
        )
        await Store.schedule_retry(db, task.id, next_retry)
        if task.signal_id:
            await Store.update_signal_status(
                db, task.signal_id, task.tenant_id, "received",
                error=None,
            )
    else:
        _log.error("Task %d permanently failed: %s", task.id, exc)
        await Store.fail_task_permanently(db, task.id, str(exc))


async def process_one_task(db: AsyncSession, task) -> None:
    """Process a single task (dispatch by type)."""
    from sqlalchemy import text
    await db.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(task.tenant_id)})
    try:
        if task.task_type == "signal_process":
            await process_signal_task(db, task)
        elif task.task_type == "re_embed":
            await process_re_embed_task(db, task)
        elif task.task_type == "lakehouse_export":
            await process_lakehouse_export_task(db, task)
        else:
            await Store.fail_task_permanently(db, task.id, f"Unknown task_type: {task.task_type}")
            return

        await Store.complete_task(db, task.id)
        _log.info("Task %d completed", task.id)

    except Exception as exc:
        _log.exception("Task %d error: %s", task.id, exc)
        # A failed flush taints the session (PendingRollbackError); roll back
        # first so we can write the failure record and avoid poisoning the
        # next tasks. set_config must be re-applied since rollback resets the GUC.
        try:
            await db.rollback()
            # rollback() expires every attribute on `task` (id, tenant_id,
            # retry_count, payload, ...) — the set_config call below and
            # handle_failure() both read them next, and an expired attribute
            # triggers an implicit synchronous reload that an AsyncSession
            # can't perform (MissingGreenlet). Refresh explicitly first;
            # refresh() itself only needs the identity key, not a live
            # attribute, so it's safe to call while everything is expired.
            await db.refresh(task)
            await db.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(task.tenant_id)})
        except Exception:
            pass
        await handle_failure(db, task, exc)
    finally:
        try:
            await db.execute(text("SELECT set_config('app.tenant_id', '', false)"))
        except Exception:
            pass


async def _export_scheduler(factory) -> None:
    """Nightly scheduler: enqueue a lakehouse_export task per active tenant once per day.

    Runs every EXPORT_SCHEDULER_INTERVAL_S seconds. When UTC hour reaches
    EXPORT_HOUR_UTC and no non-failed export task exists for (tenant, today),
    a task is enqueued. Restart-safe: misses are caught on next wake-up.
    """
    from datetime import date
    from datetime import datetime as dt

    _log.info(
        "Export scheduler started (hour=%d UTC, interval=%.0fs)",
        config.EXPORT_HOUR_UTC, config.EXPORT_SCHEDULER_INTERVAL_S,
    )
    while _running:
        await asyncio.sleep(config.EXPORT_SCHEDULER_INTERVAL_S)
        if not _running:
            break
        now = dt.now(timezone.utc)
        if now.hour < config.EXPORT_HOUR_UTC:
            continue
        today = date.today().isoformat()
        try:
            async with factory() as db:
                tenants = await Store.list_active_tenants(db)
                for tenant in tenants:
                    already = await Store.has_export_task_for_date(db, tenant.id, today)
                    if not already:
                        await Store.create_lakehouse_export_task(db, tenant.id, today)
                        _log.info(
                            "Enqueued lakehouse_export for tenant %d date=%s",
                            tenant.id, today,
                        )
        except Exception as exc:
            _log.exception("Export scheduler error: %s", exc)


async def main():
    """Worker main loop."""
    _log.info("Worker starting. Poll interval: %.1fs, batch size: %d, max retries: %d",
              config.WORKER_POLL_INTERVAL_S, config.WORKER_BATCH_SIZE, config.TASK_MAX_RETRIES)

    # The worker has to scan the RLS-forced `task` table across all tenants.
    # The restricted app role sees zero rows without the GUC set (fail-closed),
    # so task claiming uses an admin (superuser, RLS-bypass) session. Tenant
    # isolation is preserved inside process_one_task via the GUC plus a
    # query-level tenant_id filter.
    from .db.database import get_admin_async_session_factory

    factory = get_admin_async_session_factory()

    scheduler_task: asyncio.Task | None = None
    if config.EXPORT_ENABLED:
        scheduler_task = asyncio.create_task(_export_scheduler(factory))
        _log.info("Nightly export scheduler enabled (hour=%d UTC)", config.EXPORT_HOUR_UTC)

    try:
        while _running:
            _write_heartbeat()
            try:
                async with factory() as db:
                    tasks = await Store.get_next_task(
                        db, batch_size=config.WORKER_BATCH_SIZE, task_types=config.WORKER_TASK_TYPES,
                    )
                    if tasks:
                        _log.info("Fetched %d tasks", len(tasks))
                        for task in tasks:
                            if not _running:
                                break
                            await process_one_task(db, task)

            except Exception as exc:
                _log.exception("Worker loop error: %s", exc)

            if _running:
                await asyncio.sleep(config.WORKER_POLL_INTERVAL_S)
    finally:
        if scheduler_task is not None:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass

    _log.info("Worker shutdown complete")


def _find_metric_def(pack_def: dict, metric_key: str) -> dict | None:
    for m in pack_def.get("metrics", []):
        if m.get("key") == metric_key:
            return m
    return None


def _write_heartbeat() -> None:
    """Touch the heartbeat file so the container healthcheck can detect a stalled loop."""
    try:
        with open(config.WORKER_HEARTBEAT_FILE, "w") as f:
            f.write(datetime.now(timezone.utc).isoformat())
    except OSError as exc:
        _log.warning("Failed to write worker heartbeat: %s", exc)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    asyncio.run(main())
