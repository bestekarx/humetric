"""Worker process — async task queue isleme (Spec 024).

PostgreSQL SELECT FOR UPDATE SKIP LOCKED ile kuyruktan task ceker,
extract → curate → write metrics → re-embed pipeline'ini calistirir.
Exponential backoff retry, graceful shutdown (SIGTERM/SIGINT).
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from . import config, kvkk
from .agents import curator, extractor
from .db.database import get_tenant_db
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
    """Tek bir signal task'ini isler: extract → curate → write → embed."""
    payload = task.payload
    entity_id = payload.get("entity_id")
    text = payload.get("text", "")
    pack_def = payload.get("pack_definition", {})

    entity = await Store.get_entity(db, entity_id, task.tenant_id)
    if not entity:
        raise ValueError(f"Entity not found: {entity_id}")

    ctx = entity.free_text or ""
    pack_extraction_prompt = (pack_def.get("prompts", {}) or {}).get("extraction")
    pack_metrics = pack_def.get("metrics", []) or []
    extracted = await extractor.extract_metrics(
        text, ctx,
        pack_prompt=pack_extraction_prompt,
        pack_metrics=pack_metrics,
    )
    existing_metrics = await Store.get_entity_metrics(db, entity_id, task.tenant_id)
    final_metrics = await curator.curate_metrics(extracted, existing_metrics, ctx, pack_def)

    atlanan_hassas: list[str] = []

    for fm in final_metrics:
        metric_def = _find_metric_def(pack_def, fm.metric_key)
        if metric_def and metric_def.get("sensitive"):
            consent_scope = metric_def.get("requires_consent_scope")
            if consent_scope:
                has_consent = await kvkk.check_consent_for_metric(
                    db, entity_id, consent_scope, task.tenant_id,
                )
                if not has_consent:
                    atlanan_hassas.append(fm.metric_key)
                    continue

        trace = {
            "extracted": [
                e.model_dump() for e in extracted if e.metric_key == fm.metric_key
            ]
        }
        await Store.upsert_metric(db, {
            "tenant_id": task.tenant_id,
            "entity_id": entity_id,
            "metric_key": fm.metric_key,
            "value": fm.value,
            "confidence": fm.confidence,
            "source_count": 1,
            "signal_id": task.signal_id,
            "trace_data": trace,
        })

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
            "source_count": 1,
            "source_signal_id": task.signal_id,
        }
        for fm in final_metrics
    ]

    await Store.update_signal_status(
        db, task.signal_id, task.tenant_id, "completed",
        result={"metrics": result_metrics},
    )


async def process_re_embed_task(db: AsyncSession, task) -> None:
    """embedding_pending=true olan entity'yi yeniden embed eder."""
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


async def handle_failure(db: AsyncSession, task, exc: Exception) -> None:
    """Hata durumunda retry veya permanent fail karari verir."""
    from .embeddings import EmbeddingProvider

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
    """Tek bir task isleme (dispatch by type)."""
    from sqlalchemy import text
    await db.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(task.tenant_id)})
    try:
        if task.task_type == "signal_process":
            await process_signal_task(db, task)
        elif task.task_type == "re_embed":
            await process_re_embed_task(db, task)
        else:
            await Store.fail_task_permanently(db, task.id, f"Unknown task_type: {task.task_type}")
            return

        await Store.complete_task(db, task.id)
        _log.info("Task %d completed", task.id)

    except Exception as exc:
        _log.exception("Task %d error: %s", task.id, exc)
        # Hatali flush session'i kirletir (PendingRollbackError); failure
        # kaydini yazabilmek ve sonraki task'lari zehirlememek icin once
        # rollback yap. set_config tekrar gerekli cunku rollback GUC'u sifirlar.
        try:
            await db.rollback()
            await db.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(task.tenant_id)})
        except Exception:
            pass
        await handle_failure(db, task, exc)
    finally:
        try:
            await db.execute(text("SELECT set_config('app.tenant_id', '', false)"))
        except Exception:
            pass


async def main():
    """Worker main loop."""
    _log.info("Worker starting. Poll interval: %.1fs, batch size: %d, max retries: %d",
              config.WORKER_POLL_INTERVAL_S, config.WORKER_BATCH_SIZE, config.TASK_MAX_RETRIES)

    # Worker, RLS-forced `task` tablosunu tum tenant'lar icin taramak
    # zorunda. Kisitli app rolu (saha_app) GUC set edilmeden sifir satir
    # gorur (fail-closed), bu yuzden task claim'i admin (superuser, RLS
    # bypass) session ile yapilir. Tenant izolasyonu process_one_task
    # icinde GUC + sorgu seviyesinde tenant_id filtresiyle korunur.
    from .db.database import get_admin_async_session_factory

    factory = get_admin_async_session_factory()
    while _running:
        try:
            async with factory() as db:
                tasks = await Store.get_next_task(db, batch_size=config.WORKER_BATCH_SIZE)
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

    _log.info("Worker shutdown complete")


def _find_metric_def(pack_def: dict, metric_key: str) -> dict | None:
    for m in pack_def.get("metrics", []):
        if m.get("key") == metric_key:
            return m
    return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    asyncio.run(main())
