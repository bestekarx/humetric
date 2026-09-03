"""Worker process — async task queue processing (Spec 024).

Pulls tasks from the queue with PostgreSQL SELECT FOR UPDATE SKIP LOCKED
and runs the extract → curate → write metrics → re-embed pipeline.
Exponential backoff retry, graceful shutdown (SIGTERM/SIGINT).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import signal
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from . import config, kvkk
from .agents import curator, extractor
from .store import Store, _build_embed_text_safe

_log = logging.getLogger(__name__)

_running = True

# Day-one quarantine for the instruction-injection gap: a verbatim match no
# longer clears review on its own if the cited span itself reads like an
# instruction to the model rather than an observation about the entity.
# Not a full defense (see docs/plans/ozellik-arastirmasi.md #05) — cheap
# imperative patterns only, applied to the ~1-2 sentence span, not the whole
# signal, so it stays fast and low-noise.
_INJECTION_QUARANTINE_PATTERN = re.compile(
    r"system\s*override"
    r"|ignore\s+(all\s+|previous\s+|prior\s+)*instructions?"
    r"|do\s+not\s+flag"
    r"|no\s+(need\s+for\s+|need\s+)?review"
    r"|incelemeye\s+gerek\s+yok"
    r"|(set|give)\s+\S+\s+to\s+1\.0"
    r"|t(ü|u)m\s+metrikleri.*ver",
    re.IGNORECASE,
)


def _source_span_verified(source_span: str | None, signal_text: str) -> bool:
    """Whether the model's cited source_span verbatim-matches the signal text.

    Whitespace is normalised on both sides so line-wrap/formatting variation
    in the model's quote doesn't produce a false negative. A missing span is
    treated as verified — there's nothing to check, and pack extraction
    prompts are free to omit it.

    A verbatim match is necessary but not sufficient: if the cited span
    itself matches an injection-quarantine pattern, it's treated as
    unverified even though it's present in the text, so the metric still
    routes to pending_review instead of writing straight through.
    """
    if not source_span:
        return True
    if _INJECTION_QUARANTINE_PATTERN.search(source_span):
        return False
    return " ".join(source_span.split()) in " ".join(signal_text.split())


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
    context_hash = hash_text(ctx)
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
        signal_id=task.signal_id,
        pack_key=signal.pack_key if signal else pack_def.get("key"),
        pack_version=signal.pack_version if signal else pack_def.get("version"),
    )
    existing_metrics = await Store.get_entity_metrics(db, entity_id, task.tenant_id)

    # Deterministic merge — see agents/curator.py:finalize_merge. The curator
    # prompt's own rule is a confidence-weighted average formula, not a
    # judgment call, so this removes a Sonnet call from every signal.
    # curator_meta stays empty → trace records curator_model=None.
    curator_meta: dict = {}
    final_metrics = curator.finalize_merge(extracted, existing_metrics, pack_def)

    await _persist_signal_result(
        db, task, entity, extracted, final_metrics,
        extract_meta, curator_meta, existing_metrics, pack_def, input_hash,
        signal_text=signal_text,
        occurred_at=resolve_occurred_at(task, signal),
        context_hash=context_hash,
    )


def resolve_occurred_at(task, signal_row) -> datetime | None:
    """When the source text was produced, for the metric history timeline.

    The task payload carries it so the worker needs no extra query; the signal
    row is the fallback. Returning None lets the caller default to now().
    """
    raw = (task.payload or {}).get("occurred_at")
    if raw:
        if isinstance(raw, datetime):
            return raw
        try:
            return datetime.fromisoformat(str(raw))
        except ValueError:
            _log.warning("Unparseable occurred_at %r on task %s", raw, task.id)
    return getattr(signal_row, "occurred_at", None) if signal_row else None


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
    signal_text: str = "",
    occurred_at: datetime | None = None,
    context_hash: str | None = None,
) -> None:
    """Write final metrics (KVKK-gated), re-embed the entity, and mark the
    signal completed. Shared by the real-time worker and the batch worker."""
    entity_id = entity.id
    recorded_at = occurred_at or datetime.now(timezone.utc)
    skipped_sensitive: list[str] = []
    existing_by_key = {m.metric_key: m for m in existing_metrics}
    written_source_counts: dict[str, int] = {}
    # Per-metric extractor output, kept so the signal result can carry the
    # cited span — the trace view highlights it inside the raw text.
    evidence_by_key: dict[str, dict] = {}

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
        # History first: append_metric_history does not commit, so it rides
        # along in upsert_metric's transaction.
        first_extracted = extracted_entries[0] if extracted_entries else {}
        evidence_by_key[fm.metric_key] = first_extracted
        span_verified = _source_span_verified(first_extracted.get("source_span"), signal_text)
        if not span_verified:
            _log.warning(
                "source_span not found verbatim in signal text, flagging for review: "
                "entity=%s metric=%s signal=%s",
                entity_id, fm.metric_key, task.signal_id,
            )
        trace = {
            "extracted": extracted_entries,
            "extract_prompt_hash": extract_meta.get("prompt_hash"),
            "extract_schema_hash": extract_meta.get("schema_hash"),
            "context_hash": context_hash,
            "extract_model": extract_meta.get("model"),
            "curator_prompt_hash": curator_meta.get("prompt_hash"),
            "curator_schema_hash": curator_meta.get("schema_hash"),
            "curator_model": curator_meta.get("model"),
            "needs_review": fm.needs_review,
            "source_span_verified": span_verified,
        }
        await Store.append_metric_history(db, {
            "tenant_id": task.tenant_id,
            "entity_id": entity_id,
            "metric_key": fm.metric_key,
            "value": fm.value,
            "confidence": fm.confidence,
            "source_count": source_count,
            "prev_value": prior.value if prior else None,
            "signal_id": task.signal_id,
            "model": extract_meta.get("model"),
            "context_hash": context_hash,
            "reasoning": first_extracted.get("reasoning") or None,
            "source_span": first_extracted.get("source_span") or None,
            "recorded_at": recorded_at,
        })
        await Store.upsert_metric(db, {
            "tenant_id": task.tenant_id,
            "entity_id": entity_id,
            "metric_key": fm.metric_key,
            "value": fm.value,
            "confidence": fm.confidence,
            "source_count": source_count,
            "signal_id": task.signal_id,
            "last_updated": recorded_at,
            "trace_data": trace,
            "input_hash": input_hash,
            "prompt_hash": extract_meta.get("prompt_hash"),
            "schema_hash": extract_meta.get("schema_hash"),
            "context_hash": context_hash,
            "model": extract_meta.get("model"),
            "extraction_raw": {"extracted": extracted_entries},
            "review_status": "pending_review" if (fm.needs_review or not span_verified) else None,
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
            "reasoning": evidence_by_key.get(fm.metric_key, {}).get("reasoning"),
            "source_span": evidence_by_key.get(fm.metric_key, {}).get("source_span"),
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


# Tables included in a user data export, mapped to the Store method that
# yields the tenant's rows for that table, keyset-paginated.
_USER_EXPORT_TABLES: list[tuple[str, str]] = [
    ("entities", "list_all_entities_for_tenant"),
    ("metrics", "list_all_metrics_for_tenant"),
    ("metric_history", "list_all_metric_history_for_tenant"),
    ("signals", "list_all_signals_for_tenant"),
    ("usage_records", "list_all_usage_records_for_tenant"),
    ("transactions", "list_all_metering_records_for_tenant"),
]


def _row_to_dict(obj) -> dict:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


async def _write_table_file(
    db: AsyncSession, tenant_id: int, table_name: str, list_fn, out_dir, fmt: str,
) -> None:
    """Page through a table for one tenant and write it as one JSON/CSV file."""
    import csv as csv_module

    after_id = None
    rows: list[dict] = []
    while True:
        batch = await list_fn(db, tenant_id, after_id=after_id, limit=config.USER_EXPORT_BATCH_SIZE)
        if not batch:
            break
        rows.extend(_row_to_dict(obj) for obj in batch)
        after_id = batch[-1].id
        if len(batch) < config.USER_EXPORT_BATCH_SIZE:
            break

    if fmt == "csv":
        path = out_dir / f"{table_name}.csv"
        if not rows:
            path.write_text("")
            return
        fieldnames = list(rows[0].keys())
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv_module.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    k: (json.dumps(v, default=str) if isinstance(v, (dict, list)) else v)
                    for k, v in row.items()
                })
    else:
        path = out_dir / f"{table_name}.json"
        path.write_text(json.dumps(rows, default=str, indent=2))


async def process_export_request_task(db: AsyncSession, task) -> None:
    """Gather all of a tenant's raw data, zip it, save locally, email it."""
    import tempfile
    import zipfile
    from datetime import timedelta
    from pathlib import Path

    export = await Store.get_export_by_task_id(db, task.id)
    if export is None:
        raise ValueError(f"No user_export row for task {task.id}")

    await Store.mark_export_processing(db, export.id)
    fmt = task.payload.get("format", "json")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for table_name, method_name in _USER_EXPORT_TABLES:
            list_fn = getattr(Store, method_name)
            await _write_table_file(db, task.tenant_id, table_name, list_fn, tmp_path, fmt)

        zip_name = f"tenant_{task.tenant_id}_export_{export.id}.zip"
        dest_dir = config.USER_EXPORT_LOCAL_DIR
        dest_dir.mkdir(parents=True, exist_ok=True)
        zip_path = dest_dir / zip_name
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in tmp_path.iterdir():
                zf.write(f, arcname=f.name)

    from .services.email_service import send_email_with_attachment

    sent = await send_email_with_attachment(
        to_email=export.recipient_email,
        subject="Your HuMetric data export is ready",
        html_body=(
            "<p>Your requested data export is attached as a zip file. "
            "It will be kept on our servers for "
            f"{config.USER_EXPORT_RETENTION_DAYS} days and then deleted.</p>"
        ),
        attachment_path=zip_path,
        attachment_filename=zip_name,
    )
    if not sent:
        raise RuntimeError("Failed to send export email")

    expires_at = datetime.now(timezone.utc) + timedelta(days=config.USER_EXPORT_RETENTION_DAYS)
    await Store.mark_export_completed(
        db, export.id, str(zip_path.relative_to(dest_dir)), expires_at,
    )
    _log.info("Export task %d completed for tenant %d -> %s", task.id, task.tenant_id, zip_name)


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
        if task.task_type == "export_request":
            export = await Store.get_export_by_task_id(db, task.id)
            if export:
                await Store.mark_export_failed(db, export.id, str(exc))


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
        elif task.task_type == "export_request":
            await process_export_request_task(db, task)
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


async def _trial_expiry_scheduler(factory) -> None:
    """Periodically drop expired Pro trials down to the `free` tier.

    Runs every TRIAL_SWEEP_INTERVAL_S; scans all tenants using an admin
    (RLS-bypass) session. Even if a sweep is missed, dashboard reads apply
    the same demotion lazily, so a trial can never be extended.
    """
    from .services.trial_service import expire_due_trials

    _log.info("Trial expiry scheduler started (interval=%.0fs)", config.TRIAL_SWEEP_INTERVAL_S)
    while _running:
        try:
            async with factory() as db:
                count = await expire_due_trials(db)
                if count:
                    _log.info("Trial sweep downgraded %d tenant(s) to free", count)
        except Exception as exc:
            _log.exception("Trial expiry scheduler error: %s", exc)
        await asyncio.sleep(config.TRIAL_SWEEP_INTERVAL_S)


async def _run_export_cleanup_once(db: AsyncSession) -> int:
    """Delete expired user-export zip files + tracking rows. Returns count removed."""
    now = datetime.now(timezone.utc)
    expired = await Store.list_expired_exports(db, now)
    for export in expired:
        if export.file_path:
            path = config.USER_EXPORT_LOCAL_DIR / export.file_path
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                _log.warning("Could not delete export file %s: %s", path, exc)
        await Store.delete_export_record(db, export.id)
    return len(expired)


async def _user_export_cleanup_scheduler(factory) -> None:
    """Delete expired user-export zip files + rows every USER_EXPORT_CLEANUP_INTERVAL_S."""
    _log.info(
        "User export cleanup scheduler started (interval=%.0fs, retention=%dd)",
        config.USER_EXPORT_CLEANUP_INTERVAL_S, config.USER_EXPORT_RETENTION_DAYS,
    )
    while _running:
        try:
            async with factory() as db:
                removed = await _run_export_cleanup_once(db)
                if removed:
                    _log.info("User export cleanup removed %d expired export(s)", removed)
        except Exception as exc:
            _log.exception("User export cleanup scheduler error: %s", exc)
        await asyncio.sleep(config.USER_EXPORT_CLEANUP_INTERVAL_S)


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

    trial_task = asyncio.create_task(_trial_expiry_scheduler(factory))
    export_cleanup_task = asyncio.create_task(_user_export_cleanup_scheduler(factory))

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
        for bg_task in (scheduler_task, trial_task, export_cleanup_task):
            if bg_task is not None:
                bg_task.cancel()
                try:
                    await bg_task
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
