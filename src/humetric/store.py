"""Data access layer — async SQLAlchemy operations.

CRUD operations for Entity, Tenant, ApiKey, Consent, AuditLog, Signal.
All operations run on an async session.
"""

from __future__ import annotations

import base64
import logging
import os as _os
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import String, desc, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import auth, config
from .db.models import (
    ApiKey,
    AuditLog,
    Consent,
    Entity,
    EntityMetric,
    EntityMetricHistory,
    LlmCallRecord,
    MeteringRecord,
    MetricPack,
    Signal,
    Task,
    Tenant,
    UsageRecord,
    UserExport,
)

# Time buckets the chronological batch claim understands (date_trunc units).
_BATCH_WINDOW_UNITS = frozenset({"day", "week", "month"})


def _day_start(d: date) -> datetime:
    """Midnight UTC on `d`, for half-open range filters on timestamptz columns.

    Comparing against the raw column keeps the index usable; date(col) would
    not, and would also be evaluated in the session timezone.
    """
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)

_log = logging.getLogger(__name__)


class Store:
    """Async data access layer."""

    # --- Tenant ---

    @staticmethod
    async def create_tenant(db: AsyncSession, data: dict) -> Tenant:
        tenant = Tenant(**data)
        db.add(tenant)
        await db.flush()
        await db.commit()
        return tenant

    @staticmethod
    async def get_tenant_by_code(db: AsyncSession, code: str) -> Tenant | None:
        result = await db.execute(select(Tenant).where(Tenant.code == code))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_tenant_by_id(db: AsyncSession, tenant_id: int) -> Tenant | None:
        result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        return result.scalar_one_or_none()

    # --- Entity ---

    @staticmethod
    async def create_entity(db: AsyncSession, data: dict) -> Entity:
        entity = Entity(**data)
        db.add(entity)
        await db.flush()
        await db.commit()
        return entity

    @staticmethod
    async def get_entity(db: AsyncSession, entity_id: str, tenant_id: int) -> Entity | None:
        result = await db.execute(
            select(Entity).where(
                Entity.id == entity_id,
                Entity.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def upsert_entity(db: AsyncSession, data: dict) -> Entity:
        entity_id = data["id"]
        tenant_id = data["tenant_id"]

        existing = await db.execute(
            select(Entity).where(
                Entity.id == entity_id,
                Entity.tenant_id == tenant_id,
            )
        )
        entity = existing.scalar_one_or_none()

        if entity:
            for key, value in data.items():
                if key not in ("id", "tenant_id", "created_at"):
                    setattr(entity, key, value)
            entity.updated_at = datetime.now(timezone.utc)
        else:
            entity = Entity(**data)

        db.add(entity)
        await db.flush()
        await db.commit()
        return entity

    @staticmethod
    async def list_entities(
        db: AsyncSession, tenant_id: int, entity_type: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> list[Entity]:
        stmt = select(Entity).where(Entity.tenant_id == tenant_id)
        if entity_type:
            stmt = stmt.where(Entity.entity_type == entity_type)
        stmt = stmt.order_by(Entity.created_at.desc()).offset(offset).limit(limit)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    # --- EntityMetric ---

    @staticmethod
    async def get_entity_metrics(
        db: AsyncSession, entity_id: str, tenant_id: int,
    ) -> list[EntityMetric]:
        result = await db.execute(
            select(EntityMetric).where(
                EntityMetric.entity_id == entity_id,
                EntityMetric.tenant_id == tenant_id,
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_metric_with_trace(
        db: AsyncSession, entity_id: str, tenant_id: int, metric_key: str,
    ) -> EntityMetric | None:
        result = await db.execute(
            select(EntityMetric).where(
                EntityMetric.entity_id == entity_id,
                EntityMetric.tenant_id == tenant_id,
                EntityMetric.metric_key == metric_key,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def upsert_metric(db: AsyncSession, data: dict) -> EntityMetric:
        entity_id = data["entity_id"]
        metric_key = data["metric_key"]

        existing = await db.execute(
            select(EntityMetric).where(
                # RLS already scopes this, but the query must be correct on its
                # own: two tenants can share an entity_id + metric_key pair, and
                # without this filter a missing app.tenant_id GUC returns zero
                # rows, the code inserts, and uq_entity_metric_key blows up.
                EntityMetric.tenant_id == data["tenant_id"],
                EntityMetric.entity_id == entity_id,
                EntityMetric.metric_key == metric_key,
            )
        )
        metric = existing.scalar_one_or_none()

        if metric is None:
            metric = EntityMetric(**data)
        else:
            new_last_updated = data.get("last_updated") or datetime.now(timezone.utc)
            if metric.last_updated is not None and new_last_updated < metric.last_updated:
                # An out-of-order write — a backfilled signal that occurred
                # before the value currently stored. entity_metric holds the
                # LATEST value, so the payload is skipped wholesale; guarding
                # only last_updated (as this did) produced a row whose
                # timestamp and value came from different signals and
                # contradicted entity_metric_history.
                #
                # Nothing is lost: the history row is written unconditionally
                # with its own recorded_at (worker._persist_signal_result), so
                # the older signal still shows up in /explain and /history.
                # source_count is the exception — it counts contributions,
                # which is order-independent.
                if "source_count" in data:
                    metric.source_count = max(metric.source_count, data["source_count"])
            else:
                # `<` above, not `<=`: backfilled occurred_at is often
                # date-precision, so treating equal timestamps as stale would
                # freeze the metric after the first signal of each day.
                for key, value in data.items():
                    if key not in ("id", "entity_id", "metric_key", "tenant_id", "created_at", "last_updated"):
                        setattr(metric, key, value)
                metric.last_updated = new_last_updated

        db.add(metric)
        await db.flush()
        await db.commit()
        return metric

    # --- EntityMetricHistory ---

    @staticmethod
    async def append_metric_history(db: AsyncSession, data: dict) -> None:
        """Record one point of a metric's time series.

        Deliberately does NOT commit: call this immediately before
        ``upsert_metric``, whose commit lands both writes in one transaction.
        """
        db.add(EntityMetricHistory(**data))
        await db.flush()

    @staticmethod
    async def list_metric_history(
        db: AsyncSession,
        entity_id: str,
        tenant_id: int,
        metric_key: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[EntityMetricHistory], int]:
        """Return (points ordered oldest-first, total matching count)."""
        from sqlalchemy import func

        conditions = [
            EntityMetricHistory.tenant_id == tenant_id,
            EntityMetricHistory.entity_id == entity_id,
            EntityMetricHistory.metric_key == metric_key,
        ]
        if since:
            conditions.append(EntityMetricHistory.recorded_at >= since)
        if until:
            conditions.append(EntityMetricHistory.recorded_at <= until)

        total = await db.scalar(
            select(func.count()).select_from(EntityMetricHistory).where(*conditions)
        )
        result = await db.execute(
            select(EntityMetricHistory)
            .where(*conditions)
            .order_by(EntityMetricHistory.recorded_at.asc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all()), int(total or 0)

    @staticmethod
    async def list_metric_contributions(
        db: AsyncSession,
        entity_id: str,
        tenant_id: int,
        metric_key: str,
        *,
        limit: int = 10,
    ) -> list[EntityMetricHistory]:
        """Most recent history points for a metric, newest first."""
        result = await db.execute(
            select(EntityMetricHistory)
            .where(
                EntityMetricHistory.tenant_id == tenant_id,
                EntityMetricHistory.entity_id == entity_id,
                EntityMetricHistory.metric_key == metric_key,
            )
            .order_by(EntityMetricHistory.recorded_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    # --- Signal (Spec 022) ---

    @staticmethod
    async def create_signal(db: AsyncSession, data: dict) -> Signal:
        signal = Signal(**data)
        db.add(signal)
        await db.flush()
        await db.commit()
        return signal

    @staticmethod
    async def get_signal(db: AsyncSession, signal_id: str, tenant_id: int) -> Signal | None:
        result = await db.execute(
            select(Signal).where(
                Signal.id == signal_id,
                Signal.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_signals_for_entity(
        db: AsyncSession,
        entity_id: str,
        tenant_id: int,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Signal], int]:
        """Return (signals newest-first, total matching count)."""
        from sqlalchemy import desc, func

        conditions = [Signal.tenant_id == tenant_id, Signal.entity_id == entity_id]
        if status:
            conditions.append(Signal.status == status)

        total = await db.scalar(
            select(func.count()).select_from(Signal).where(*conditions)
        )
        result = await db.execute(
            select(Signal)
            .where(*conditions)
            .order_by(desc(Signal.created_at))
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all()), int(total or 0)

    @staticmethod
    async def update_signal_status(
        db: AsyncSession, signal_id: str, tenant_id: int,
        status: str, result: dict | None = None, error: str | None = None,
        clear_error: bool = False,
    ) -> Signal | None:
        signal = await Store.get_signal(db, signal_id, tenant_id)
        if not signal:
            return None
        signal.status = status
        if result:
            signal.result = {**signal.result, **result}
        if error:
            signal.error = error
        elif clear_error:
            signal.error = None
        if status in ("completed", "failed"):
            signal.processed_at = datetime.now(timezone.utc)
        db.add(signal)
        await db.flush()
        await db.commit()
        return signal

    # --- UsageRecord (Spec 022) ---

    @staticmethod
    async def record_usage(
        db: AsyncSession, data: dict,
    ) -> UsageRecord:
        record = UsageRecord(**data)
        db.add(record)
        await db.commit()
        return record

    # Dimensions the call report can group on, mapped to the column that
    # carries the group label. Anything outside this dict is rejected by the
    # endpoint before it reaches SQL.
    USAGE_GROUP_COLUMNS = {
        "tool": UsageRecord.tool_name,
        "client": UsageRecord.client,
        "key": UsageRecord.api_key_id,
        "endpoint": UsageRecord.endpoint,
        "day": func.date(UsageRecord.created_at),
    }

    @staticmethod
    async def aggregate_usage_calls(
        db: AsyncSession,
        tenant_id: int,
        *,
        start_date: date,
        end_date: date,
        group_by: str,
        client: str | None = None,
        api_key_id: int | None = None,
        tool_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Grouped call counts over usage_record.

        ``tool_calls`` counts distinct call_ids, falling back to the row id when
        a request carries none: one MCP tool call fans out to several requests
        and must count once, while a plain REST request has no call_id and is a
        call in its own right. ``http_requests`` is the raw row count, so the
        gap between the two columns is the fan-out.
        """
        group_col = Store.USAGE_GROUP_COLUMNS[group_by]

        # COALESCE to the row id keeps call-less requests from collapsing into
        # a single NULL group and vanishing from the count.
        call_key = func.coalesce(
            UsageRecord.call_id, func.cast(UsageRecord.id, String)
        )

        q = (
            select(
                group_col.label("group_value"),
                func.count(func.distinct(call_key)).label("tool_calls"),
                func.count().label("http_requests"),
                func.count().filter(UsageRecord.status_code >= 400).label("error_count"),
                func.avg(UsageRecord.duration_ms).label("avg_duration_ms"),
            )
            .where(
                UsageRecord.tenant_id == tenant_id,
                func.date(UsageRecord.created_at) >= start_date,
                func.date(UsageRecord.created_at) <= end_date,
            )
            .group_by(group_col)
            .order_by(desc("http_requests"))
            .limit(limit)
            .offset(offset)
        )
        if client:
            q = q.where(UsageRecord.client == client)
        if api_key_id is not None:
            q = q.where(UsageRecord.api_key_id == api_key_id)
        if tool_name:
            q = q.where(UsageRecord.tool_name == tool_name)

        rows = (await db.execute(q)).all()
        out = [
            {
                "group": "(none)" if r.group_value is None else str(r.group_value),
                "tool_calls": r.tool_calls or 0,
                "http_requests": r.http_requests or 0,
                "error_count": r.error_count or 0,
                "avg_duration_ms": int(r.avg_duration_ms) if r.avg_duration_ms is not None else None,
            }
            for r in rows
        ]

        # A bare key id is unreadable in a report; the label is what identifies
        # the human behind it. Looked up separately rather than joined so the
        # aggregate query stays the same shape for every grouping.
        if group_by == "key":
            ids = [int(r["group"]) for r in out if r["group"] != "(none)"]
            if ids:
                labels = dict(
                    (await db.execute(
                        select(ApiKey.id, ApiKey.label).where(ApiKey.id.in_(ids))
                    )).all()
                )
                for row in out:
                    if row["group"] != "(none)":
                        row["api_key_label"] = labels.get(int(row["group"]))

        return out

    @staticmethod
    async def list_usage_calls(
        db: AsyncSession,
        tenant_id: int,
        *,
        start_date: date,
        end_date: date,
        client: str | None = None,
        api_key_id: int | None = None,
        tool_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[UsageRecord]:
        """Ungrouped rows, newest first — the group_by=none path."""
        q = select(UsageRecord).where(
            UsageRecord.tenant_id == tenant_id,
            func.date(UsageRecord.created_at) >= start_date,
            func.date(UsageRecord.created_at) <= end_date,
        )
        if client:
            q = q.where(UsageRecord.client == client)
        if api_key_id is not None:
            q = q.where(UsageRecord.api_key_id == api_key_id)
        if tool_name:
            q = q.where(UsageRecord.tool_name == tool_name)

        q = q.order_by(desc(UsageRecord.created_at)).limit(limit).offset(offset)
        return list((await db.execute(q)).scalars().all())

    @staticmethod
    async def aggregate_llm_calls_by_pack(
        db: AsyncSession,
        tenant_id: int,
        *,
        start_date: date,
        end_date: date,
    ) -> list[dict]:
        """Token totals per pack over llm_call_record, with the most
        frequently used pack_version and provider:model label for that pack
        in the window."""
        model_label = func.concat(
            func.coalesce(LlmCallRecord.provider, "unknown"), ":",
            func.coalesce(LlmCallRecord.model, "unknown"),
        )
        q = (
            select(
                LlmCallRecord.pack_key.label("pack_key"),
                # mode(), not max(): max() would report v3 next to v1+v2+v3's
                # summed tokens if the pack was bumped mid-window. The modal
                # version is the one that actually spent most of them, and it
                # matches how the model label below is picked.
                func.mode().within_group(LlmCallRecord.pack_version).label("pack_version"),
                func.sum(LlmCallRecord.token_count).label("llm_token_count"),
                func.mode().within_group(model_label).label("model"),
            )
            .where(
                LlmCallRecord.tenant_id == tenant_id,
                LlmCallRecord.pack_key.is_not(None),
                # Half-open range on the raw column rather than date(created_at):
                # a function call on the column is not sargable and would skip
                # ix_llm_call_record_tenant_created.
                LlmCallRecord.created_at >= _day_start(start_date),
                LlmCallRecord.created_at < _day_start(end_date + timedelta(days=1)),
            )
            .group_by(LlmCallRecord.pack_key)
        )
        rows = (await db.execute(q)).all()
        return [
            {
                "pack_key": r.pack_key,
                "pack_version": r.pack_version,
                "llm_token_count": int(r.llm_token_count or 0),
                "model": r.model,
            }
            for r in rows
        ]

    @staticmethod
    async def count_signals_by_pack(
        db: AsyncSession,
        tenant_id: int,
        *,
        start_date: date,
        end_date: date,
    ) -> list[dict]:
        """Signal + distinct-entity counts per pack over the signal table."""
        q = (
            select(
                Signal.pack_key.label("pack_key"),
                func.count().label("signal_count"),
                func.count(func.distinct(Signal.entity_id)).label("entity_count"),
            )
            .where(
                Signal.tenant_id == tenant_id,
                Signal.pack_key.is_not(None),
                # Windowed on ingest time (created_at), not occurred_at: this
                # answers "what did we spend in this period", and a backfill
                # spends on the day it runs. Same sargable form as above.
                Signal.created_at >= _day_start(start_date),
                Signal.created_at < _day_start(end_date + timedelta(days=1)),
            )
            .group_by(Signal.pack_key)
        )
        rows = (await db.execute(q)).all()
        return [
            {
                "pack_key": r.pack_key,
                "signal_count": r.signal_count,
                "entity_count": r.entity_count,
            }
            for r in rows
        ]

    # --- ApiKey ---

    @staticmethod
    async def create_api_key(
        db: AsyncSession,
        tenant_id: int,
        prefix: str,
        label: str | None,
        scopes: list[str],
        expires_at: datetime | None = None,
    ) -> tuple[str, ApiKey]:
        full_key, key_hash = auth.generate_api_key(prefix)
        api_key = ApiKey(
            tenant_id=tenant_id,
            prefix=prefix,
            key_hash=key_hash,
            scopes=scopes,
            label=label,
            expires_at=expires_at,
        )
        db.add(api_key)
        # flush (not refresh-after-commit) populates the autoincrement id
        # within the same transaction — the RLS GUC set by get_tenant_db()
        # is only guaranteed for the transaction it was set in, and commit()
        # releases the connection back to the pool, so a post-commit refresh
        # can land on a connection without app.tenant_id set.
        await db.flush()
        await db.commit()
        return full_key, api_key

    @staticmethod
    async def verify_and_get_api_key(
        db: AsyncSession, full_key: str,
    ) -> tuple[ApiKey | None, str | None]:
        """Return (api_key, None) on success or (api_key_or_None, reason) on failure.

        Reasons: "not_found", "revoked", "expired".
        On success the second element is None.
        """
        key_hash = auth.hash_key(full_key)
        key_prefix = full_key[:12] if len(full_key) > 12 else full_key

        result = await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        api_key = result.scalar_one_or_none()

        if api_key is None:
            _log.warning("verify_api_key: not found prefix=%s", key_prefix)
            return None, "not_found"

        if api_key.is_revoked:
            _log.warning("verify_api_key: revoked id=%s prefix=%s tenant=%s", api_key.id, key_prefix, api_key.tenant_id)
            return api_key, "revoked"

        if api_key.expires_at:
            now = datetime.now(timezone.utc)
            expires = api_key.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires < now:
                _log.warning("verify_api_key: expired id=%s prefix=%s tenant=%s expired_at=%s", api_key.id, key_prefix, api_key.tenant_id, expires)
                return api_key, "expired"

        api_key.last_used_at = datetime.now(timezone.utc)
        await db.commit()
        return api_key, None

    @staticmethod
    async def delete_api_key(db: AsyncSession, key_id: int) -> bool:
        result = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
        api_key = result.scalar_one_or_none()
        if api_key is None:
            return False
        await db.delete(api_key)
        await db.commit()
        return True

    @staticmethod
    async def list_api_keys(
        db: AsyncSession, tenant_id: int,
    ) -> list[ApiKey]:
        result = await db.execute(
            select(ApiKey).where(
                ApiKey.tenant_id == tenant_id,
                ApiKey.is_revoked == False,  # noqa: E712
            )
        )
        return list(result.scalars().all())

    # --- Consent ---

    @staticmethod
    async def create_consent(db: AsyncSession, data: dict) -> Consent:
        consent = Consent(**data)
        db.add(consent)
        await db.flush()
        await db.commit()
        return consent

    @staticmethod
    async def check_consent(
        db: AsyncSession, entity_id: str, scope: str, tenant_id: int,
    ) -> bool:
        result = await db.execute(
            select(Consent).where(
                Consent.entity_id == entity_id,
                Consent.scope == scope,
                Consent.tenant_id == tenant_id,
                Consent.status == "granted",
            )
        )
        consent = result.scalar_one_or_none()
        if consent is None:
            return False
        if consent.expires_at and consent.expires_at < datetime.now(timezone.utc):
            return False
        return True

    @staticmethod
    async def revoke_consent(
        db: AsyncSession, entity_id: str, scope: str, tenant_id: int,
    ) -> bool:
        result = await db.execute(
            select(Consent).where(
                Consent.entity_id == entity_id,
                Consent.scope == scope,
                Consent.tenant_id == tenant_id,
                Consent.status == "granted",
            )
        )
        consent = result.scalar_one_or_none()
        if consent is None:
            return False
        consent.status = "revoked"
        consent.revoked_at = datetime.now(timezone.utc)
        await db.commit()
        return True

    @staticmethod
    async def get_consents(
        db: AsyncSession, entity_id: str, tenant_id: int,
    ) -> list[Consent]:
        result = await db.execute(
            select(Consent).where(
                Consent.entity_id == entity_id,
                Consent.tenant_id == tenant_id,
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def revoke_all_consents(
        db: AsyncSession, entity_id: str, tenant_id: int,
    ) -> int:
        result = await db.execute(
            select(Consent).where(
                Consent.entity_id == entity_id,
                Consent.tenant_id == tenant_id,
                Consent.status == "granted",
            )
        )
        consents = list(result.scalars().all())
        count = 0
        for c in consents:
            c.status = "revoked"
            c.revoked_at = datetime.now(timezone.utc)
            count += 1
        if count:
            await db.commit()
        return count

    # --- AuditLog ---

    @staticmethod
    async def write_audit_log(db: AsyncSession, data: dict) -> AuditLog:
        log = AuditLog(**data)
        db.add(log)
        await db.flush()
        await db.commit()
        return log

    @staticmethod
    async def audit(
        db: AsyncSession,
        *,
        tenant_id: int,
        action: str,
        entity_id: str | None = None,
        api_key_id: int | None = None,
        details: dict | None = None,
    ) -> None:
        """Fire-and-forget audit log write; swallows errors to never block the main path."""
        try:
            log = AuditLog(
                tenant_id=tenant_id,
                action=action,
                entity_id=entity_id,
                api_key_id=api_key_id,
                details=details,
            )
            db.add(log)
            await db.commit()
        except Exception as exc:
            _log.error("audit write failed action=%s tenant=%s err=%s", action, tenant_id, exc)

    @staticmethod
    async def list_audit_logs(
        db: AsyncSession,
        tenant_id: int,
        *,
        action: str | None = None,
        entity_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLog]:
        from sqlalchemy import desc
        q = select(AuditLog).where(AuditLog.tenant_id == tenant_id)
        if action:
            q = q.where(AuditLog.action == action)
        if entity_id:
            q = q.where(AuditLog.entity_id == entity_id)
        q = q.order_by(desc(AuditLog.created_at)).limit(limit).offset(offset)
        result = await db.execute(q)
        return list(result.scalars().all())

    # --- Metric Pack (Spec 023) ---

    @staticmethod
    async def create_pack(
        db: AsyncSession, tenant_id: int, pack_key: str, version: int, definition: dict,
    ) -> MetricPack:
        pack = MetricPack(
            tenant_id=tenant_id,
            pack_key=pack_key,
            version=version,
            definition=definition,
        )
        db.add(pack)
        await db.flush()
        await db.commit()
        return pack

    @staticmethod
    async def get_pack(
        db: AsyncSession, tenant_id: int, pack_key: str,
    ) -> MetricPack | None:
        result = await db.execute(
            select(MetricPack).where(
                MetricPack.tenant_id == tenant_id,
                MetricPack.pack_key == pack_key,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_active_pack_for_type(
        db: AsyncSession, tenant_id: int, entity_type: str,
    ) -> MetricPack | None:
        result = await db.execute(
            select(MetricPack).where(
                MetricPack.tenant_id == tenant_id,
                MetricPack.is_active == True,  # noqa: E712
            )
        )
        packs = list(result.scalars().all())
        for p in packs:
            if p.definition.get("entity_type") == entity_type:
                return p
        return None

    @staticmethod
    async def list_packs(
        db: AsyncSession, tenant_id: int, is_active: bool | None = None,
    ) -> list[MetricPack]:
        stmt = select(MetricPack).where(MetricPack.tenant_id == tenant_id)
        if is_active is not None:
            stmt = stmt.where(MetricPack.is_active == is_active)
        stmt = stmt.order_by(MetricPack.created_at.desc())
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def update_pack(
        db: AsyncSession, tenant_id: int, pack_key: str, definition: dict,
    ) -> MetricPack | None:
        pack = await Store.get_pack(db, tenant_id, pack_key)
        if not pack:
            return None
        pack.version = pack.version + 1
        pack.definition = definition
        pack.updated_at = datetime.now(timezone.utc)
        db.add(pack)
        await db.flush()
        await db.commit()
        return pack

    @staticmethod
    async def entity_type_exists_in_active_pack(
        db: AsyncSession, tenant_id: int, entity_type: str,
    ) -> tuple[str | None, MetricPack | None]:
        """Is entity_type used in any active pack? Returns (pack_key, pack)."""
        packs = await Store.list_packs(db, tenant_id, is_active=True)
        for p in packs:
            if p.definition.get("entity_type") == entity_type:
                return p.pack_key, p
        return None, None

    @staticmethod
    async def entity_type_exists_in_any_pack(
        db: AsyncSession, tenant_id: int, entity_type: str,
    ) -> tuple[str | None, MetricPack | None]:
        """Does entity_type exist in any pack (active+inactive)? Returns (pack_key, pack)."""
        packs = await Store.list_packs(db, tenant_id, is_active=None)
        for p in packs:
            if p.definition.get("entity_type") == entity_type:
                return p.pack_key, p
        return None, None

    @staticmethod
    async def validate_entity_against_pack(
        db: AsyncSession, tenant_id: int, entity_type: str, fields: dict,
    ) -> MetricPack:
        """Validate the entity against the active pack. Raises HTTPException if invalid."""
        from fastapi import HTTPException

        pack = await Store.get_active_pack_for_type(db, tenant_id, entity_type)
        if not pack:
            # Is there any active pack at all?
            all_active = await Store.list_packs(db, tenant_id, is_active=True)
            if not all_active:
                code, msg = "no_active_pack_for_type", "No active packs in this tenant"
            else:
                code, msg = "unknown_entity_type", f"Entity type '{entity_type}' is not defined in any active pack"
            from .schema import error_envelope
            raise HTTPException(
                status_code=422,
                detail=error_envelope(code, msg).model_dump(),
            )

        raw_required = pack.definition.get("required_fields", [])
        required_keys = [f["key"] if isinstance(f, dict) else f for f in raw_required]
        missing = [k for k in required_keys if k not in fields]
        if missing:
            from .schema import error_envelope
            raise HTTPException(
                status_code=422,
                detail=error_envelope(
                    "missing_required_fields",
                    f"Required fields missing: {', '.join(missing)}",
                ).model_dump(),
            )

        return pack

    @staticmethod
    async def check_entity_type_writable(
        db: AsyncSession, tenant_id: int, entity_type: str,
    ) -> None:
        """Is the entity type writable? Raises 403 if there's no active pack but an inactive one exists."""
        active_pack = await Store.get_active_pack_for_type(db, tenant_id, entity_type)
        if active_pack and active_pack.is_active:
            return
        any_pack_key, any_pack = await Store.entity_type_exists_in_any_pack(db, tenant_id, entity_type)
        if any_pack and not any_pack.is_active:
            from fastapi import HTTPException
            from .schema import error_envelope
            raise HTTPException(
                status_code=403,
                detail=error_envelope(
                    "entity_type_locked",
                    f"Entity type '{entity_type}' is locked (pack inactive)",
                ).model_dump(),
            )

    # --- Hybrid Search (Spec 022) ---

    @staticmethod
    async def hybrid_search_entities(
        db: AsyncSession,
        tenant_id: int,
        query_embedding: list[float] | None = None,
        query_text: str | None = None,
        entity_type: str | None = None,
        filters: dict | None = None,
        top_k: int = 10,
    ) -> list[Entity]:
        from sqlalchemy import func, literal_column

        base = select(Entity).where(
            Entity.tenant_id == tenant_id,
            Entity.status == "active",
        )

        if entity_type:
            base = base.where(Entity.entity_type == entity_type)

        if filters:
            for key, val in filters.items():
                base = base.where(Entity.fields[key].as_string() == str(val))

        order_clauses = []

        if query_embedding is not None:
            # Use pgvector's own operator: it does the list -> vector
            # adaptation correctly (func.cosine_distance sends the raw list
            # to asyncpg expecting a str, and blows up).
            base = base.add_columns(
                Entity.embedding.cosine_distance(query_embedding).label("_dist")
            )
            base = base.where(Entity.embedding.isnot(None))
            order_clauses.append(literal_column("_dist").asc())

        if query_text and query_text.strip():
            ts_query = func.plainto_tsquery("simple", query_text)
            base = base.add_columns(
                func.ts_rank(
                    func.to_tsvector("simple", func.coalesce(Entity.free_text, "")),
                    ts_query,
                ).label("_ts_rank")
            )
            order_clauses.append(literal_column("_ts_rank").desc())

        if order_clauses:
            base = base.order_by(*order_clauses)
        else:
            base = base.order_by(Entity.created_at.desc())

        base = base.limit(top_k)

        result = await db.execute(base)
        raw_rows = result.all()

        entities: list[Entity] = []
        from collections.abc import Sequence
        for row in raw_rows:
            if isinstance(row, Sequence) and not isinstance(row, Entity):
                entities.append(row[0])
            else:
                entities.append(row)

        return entities

    # --- Task (Spec 024) ---

    @staticmethod
    async def create_task(db: AsyncSession, data: dict) -> Task:
        task = Task(**data)
        db.add(task)
        await db.flush()
        await db.commit()
        return task

    @staticmethod
    async def get_next_task(
        db: AsyncSession, batch_size: int = 5, task_types: list[str] | None = None,
    ) -> list[Task]:
        """Pull tasks from the queue with SELECT ... FOR UPDATE SKIP LOCKED.

        Pass ``task_types`` to claim only specific kinds (e.g. the batch worker
        claims only ``signal_process``); default None claims any type.
        """
        now = datetime.now(timezone.utc)
        stmt = select(Task).where(
            Task.status == "queued",
            (Task.next_retry_at.is_(None)) | (Task.next_retry_at <= now),
        )
        if task_types:
            stmt = stmt.where(Task.task_type.in_(task_types))
        result = await db.execute(
            stmt.order_by(Task.created_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        tasks = list(result.scalars().all())
        for t in tasks:
            t.status = "processing"
            t.started_at = now
        await db.commit()
        return tasks

    @staticmethod
    async def get_next_chronological_batch(
        db: AsyncSession, *, batch_size: int = 5, window: str = "week",
    ) -> list[Task]:
        """Claim the oldest time window of queued signal tasks, one per entity.

        ``get_next_task`` claims by queue order, so one batch can hold several
        signals for the same entity. Each of them then reads the same
        pre-batch metric snapshot, finds no history, takes the cold-start fast
        path, and the writes are last-write-wins — the curator never runs.

        Claiming a single *time window* at a time, and at most one signal per
        entity within it, makes every wave see the previous wave's results.
        The curator then genuinely reconciles and the metric history gets one
        ordered point per window per entity. Signals left over from a window
        (same entity, same week) are picked up by a later wave, so
        chronological order is never broken.
        """
        if window not in _BATCH_WINDOW_UNITS:
            raise ValueError(f"Unsupported batch window: {window}")

        now = datetime.now(timezone.utc)
        occurred = "COALESCE(s.occurred_at, s.created_at)"
        # `window` is whitelisted above, so interpolating it is safe.
        candidates = await db.execute(
            text(
                f"""
                WITH ready AS (
                    SELECT t.id AS task_id,
                           s.tenant_id,
                           s.entity_id,
                           {occurred} AS occurred_at,
                           date_trunc('{window}', {occurred}) AS bucket
                    FROM task t
                    JOIN signal s ON s.id = t.signal_id
                    WHERE t.status = 'queued'
                      AND t.task_type = 'signal_process'
                      AND (t.next_retry_at IS NULL OR t.next_retry_at <= :now)
                ),
                oldest AS (SELECT MIN(bucket) AS bucket FROM ready)
                SELECT DISTINCT ON (r.tenant_id, r.entity_id) r.task_id
                FROM ready r
                JOIN oldest o ON r.bucket = o.bucket
                ORDER BY r.tenant_id, r.entity_id, r.occurred_at ASC
                LIMIT :limit
                """
            ),
            {"now": now, "limit": batch_size},
        )
        task_ids = [row[0] for row in candidates.all()]
        if not task_ids:
            return []

        result = await db.execute(
            select(Task)
            .where(Task.id.in_(task_ids), Task.status == "queued")
            .with_for_update(skip_locked=True)
        )
        tasks = list(result.scalars().all())
        for t in tasks:
            t.status = "processing"
            t.started_at = now
        await db.commit()
        return tasks

    @staticmethod
    async def reclaim_stale_tasks(db: AsyncSession, older_than_s: float) -> int:
        """Requeue tasks stuck in 'processing' longer than ``older_than_s``.

        A batch worker that crashes mid-batch leaves its claimed tasks in
        'processing'. This returns them to 'queued' so a later run picks them
        up. Returns the number reclaimed.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_s)
        result = await db.execute(
            update(Task)
            .where(Task.status == "processing", Task.started_at < cutoff)
            .values(status="queued", started_at=None)
        )
        await db.commit()
        return result.rowcount or 0

    @staticmethod
    async def complete_task(db: AsyncSession, task_id: int) -> None:
        task = await db.get(Task, task_id)
        if task:
            task.status = "completed"
            task.completed_at = datetime.now(timezone.utc)
            await db.commit()

    @staticmethod
    async def fail_task_permanently(db: AsyncSession, task_id: int, error: str) -> None:
        task = await db.get(Task, task_id)
        if task:
            task.status = "failed"
            task.last_error = error
            task.completed_at = datetime.now(timezone.utc)
            if task.signal_id:
                await Store.update_signal_status(
                    db, task.signal_id, task.tenant_id, "failed", error=error,
                )
            await db.commit()

    @staticmethod
    async def schedule_retry(
        db: AsyncSession, task_id: int, next_retry_at: datetime,
    ) -> None:
        task = await db.get(Task, task_id)
        if task:
            task.status = "queued"
            task.retry_count += 1
            task.next_retry_at = next_retry_at
            task.started_at = None
            await db.commit()

    # --- Idempotency (Spec 024) ---

    @staticmethod
    async def check_idempotency(
        db: AsyncSession, tenant_id: int, external_id: str, entity_id: str,
    ) -> Signal | None:
        """Idempotency-Key check: has the same key been used within the last 24 hours?"""
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        result = await db.execute(
            select(Signal).where(
                Signal.tenant_id == tenant_id,
                Signal.external_id == external_id,
                Signal.entity_id == entity_id,
                Signal.created_at > cutoff,
            )
        )
        return result.scalars().first()

    @staticmethod
    async def find_signal_by_external_id(
        db: AsyncSession, tenant_id: int, external_id: str, entity_id: str,
    ) -> Signal | None:
        """Duplicate lookup with no time window.

        `uq_signal_idempotency` is forever, but check_idempotency() only looks
        back 24 hours — so a re-post older than that slips past the pre-flight
        check and hits the constraint as a 500. This is the lookup that turns
        it into a 409 instead: 24h is the safe replay window, anything older
        is honestly a conflict.
        """
        result = await db.execute(
            select(Signal).where(
                Signal.tenant_id == tenant_id,
                Signal.external_id == external_id,
                Signal.entity_id == entity_id,
            )
        )
        return result.scalars().first()

    # --- Embedding pending (Spec 024) ---

    @staticmethod
    async def set_embedding_pending(db: AsyncSession, entity_id: str, pending: bool) -> None:
        entity = await Store.get_entity(db, entity_id, 0)
        if entity is None:
            result = await db.execute(select(Entity).where(Entity.id == entity_id))
            entity = result.scalar_one_or_none()
        if entity:
            entity.embedding_pending = pending
            await db.commit()

    @staticmethod
    async def create_re_embed_task(db: AsyncSession, entity_id: str, tenant_id: int) -> Task:
        return await Store.create_task(db, {
            "tenant_id": tenant_id,
            "signal_id": None,
            "task_type": "re_embed",
            "status": "queued",
            "payload": {"entity_id": entity_id},
        })

    @staticmethod
    async def update_entity_embedding(
        db: AsyncSession, entity_id: str, tenant_id: int, embed_text: str,
    ) -> None:
        entity = await Store.get_entity(db, entity_id, tenant_id)
        if not entity:
            return
        try:
            from .embeddings import get_tenant_embedding_provider
            provider = await get_tenant_embedding_provider(tenant_id, db)
            vectors = await provider.embed([embed_text])
            entity.embedding = vectors[0]
            entity.embedding_text = embed_text
            entity.embedding_pending = False
            await db.commit()

            try:
                from .services.usage_service import record_embedding
                await record_embedding(tenant_id)
            except Exception:
                pass
        except Exception:
            entity.embedding_pending = True
            await db.commit()
            raise

    # --- BYO-Key (Spec 025) ---

    @staticmethod
    def _keys_dict(tenant) -> dict:
        """Build the canonical keys status dict from a Tenant row."""
        return {
            "has_anthropic_key": bool(tenant.anthropic_key_encrypted),
            "has_voyage_key": bool(tenant.voyage_key_encrypted),
            "has_openai_key": bool(tenant.openai_key_encrypted),
            "has_google_ai_key": bool(tenant.google_ai_key_encrypted),
            "has_deepseek_key": bool(tenant.deepseek_key_encrypted),
            "llm_provider": tenant.llm_provider or "anthropic",
            "updated_at": tenant.updated_at,
        }

    @staticmethod
    def _empty_keys_dict() -> dict:
        return {
            "has_anthropic_key": False,
            "has_voyage_key": False,
            "has_openai_key": False,
            "has_google_ai_key": False,
            "has_deepseek_key": False,
            "llm_provider": "anthropic",
            "updated_at": None,
        }

    @staticmethod
    async def get_tenant_keys(db: AsyncSession, tenant_id: int) -> dict:
        tenant = await Store.get_tenant_by_id(db, tenant_id)
        if not tenant:
            return Store._empty_keys_dict()
        return Store._keys_dict(tenant)

    @staticmethod
    async def upsert_tenant_keys(
        db: AsyncSession, tenant_id: int, data: dict,
    ) -> dict:
        tenant = await Store.get_tenant_by_id(db, tenant_id)
        if not tenant:
            return Store._empty_keys_dict()
        if data.get("anthropic_key") is not None:
            tenant.anthropic_key_encrypted = encrypt_key(data["anthropic_key"])
        if data.get("voyage_key") is not None:
            tenant.voyage_key_encrypted = encrypt_key(data["voyage_key"])
        if data.get("openai_key") is not None:
            tenant.openai_key_encrypted = encrypt_key(data["openai_key"])
        if data.get("google_ai_key") is not None:
            tenant.google_ai_key_encrypted = encrypt_key(data["google_ai_key"])
        if data.get("deepseek_key") is not None:
            tenant.deepseek_key_encrypted = encrypt_key(data["deepseek_key"])
        if data.get("llm_provider") is not None:
            tenant.llm_provider = data["llm_provider"]
        tenant.updated_at = datetime.now(timezone.utc)
        db.add(tenant)
        await db.commit()
        return Store._keys_dict(tenant)

    @staticmethod
    async def delete_tenant_keys(db: AsyncSession, tenant_id: int) -> dict:
        tenant = await Store.get_tenant_by_id(db, tenant_id)
        if not tenant:
            return Store._empty_keys_dict()
        tenant.anthropic_key_encrypted = None
        tenant.voyage_key_encrypted = None
        tenant.openai_key_encrypted = None
        tenant.google_ai_key_encrypted = None
        tenant.deepseek_key_encrypted = None
        # Reset provider so a tenant that removed BYO keys never gets locked out:
        # anthropic falls back to the platform key. Keeping a non-anthropic
        # provider here would leave the pipeline keyless and failing.
        tenant.llm_provider = "anthropic"
        tenant.updated_at = datetime.now(timezone.utc)
        db.add(tenant)
        await db.commit()
        return Store._empty_keys_dict()

    @staticmethod
    async def decrypt_tenant_key(
        db: AsyncSession, tenant_id: int, key_type: str,
    ) -> str | None:
        tenant = await Store.get_tenant_by_id(db, tenant_id)
        if not tenant:
            return None
        col_map = {
            "anthropic": "anthropic_key_encrypted",
            "voyage": "voyage_key_encrypted",
            "openai": "openai_key_encrypted",
            "google": "google_ai_key_encrypted",
            "deepseek": "deepseek_key_encrypted",
        }
        attr = col_map.get(key_type, "anthropic_key_encrypted")
        encrypted = getattr(tenant, attr, None)
        if not encrypted:
            return None
        return decrypt_key(encrypted)

    # --- Reviewer Override (eval/replay harness) ---

    @staticmethod
    async def set_reviewer_override(
        db: AsyncSession,
        entity_id: str,
        tenant_id: int,
        metric_key: str,
        value: float,
        confidence: float,
        comment: str = "",
    ) -> EntityMetric | None:
        result = await db.execute(
            select(EntityMetric).where(
                EntityMetric.entity_id == entity_id,
                EntityMetric.tenant_id == tenant_id,
                EntityMetric.metric_key == metric_key,
            )
        )
        metric = result.scalar_one_or_none()
        if not metric:
            return None

        metric.reviewer_override = {
            "value": value,
            "confidence": confidence,
            "comment": comment,
            "reviewer": "human",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        metric.review_status = "reviewed"
        metric.value = value
        metric.confidence = confidence
        metric.last_updated = datetime.now(timezone.utc)
        db.add(metric)
        await db.flush()
        await db.commit()
        return metric

    @staticmethod
    async def list_pending_reviews(
        db: AsyncSession, tenant_id: int, limit: int = 50,
    ) -> list[EntityMetric]:
        result = await db.execute(
            select(EntityMetric).where(
                EntityMetric.tenant_id == tenant_id,
                EntityMetric.review_status == "pending_review",
            ).limit(limit)
        )
        return list(result.scalars().all())

    # --- Analytics lakehouse export (Spec 010) ---

    @staticmethod
    async def list_active_tenants(db: AsyncSession) -> list[Tenant]:
        """Return all active tenants (admin session, no RLS)."""
        result = await db.execute(
            select(Tenant).where(Tenant.status == "active").order_by(Tenant.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def create_lakehouse_export_task(
        db: AsyncSession, tenant_id: int, export_date: str,
    ) -> Task:
        """Enqueue a lakehouse_export task for the given tenant and date."""
        return await Store.create_task(db, {
            "tenant_id": tenant_id,
            "signal_id": None,
            "task_type": "lakehouse_export",
            "status": "queued",
            "payload": {"export_date": export_date},
        })

    @staticmethod
    async def has_export_task_for_date(
        db: AsyncSession, tenant_id: int, export_date: str,
    ) -> bool:
        """Return True if a non-failed lakehouse_export task exists for (tenant, date)."""
        from sqlalchemy import and_

        result = await db.execute(
            select(Task.id).where(
                and_(
                    Task.tenant_id == tenant_id,
                    Task.task_type == "lakehouse_export",
                    Task.status.not_in(["failed"]),
                    Task.payload["export_date"].astext == export_date,
                )
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None

    # --- UserExport (user-facing raw data export, Spec: user data export) ---

    @staticmethod
    async def create_export_request(
        db: AsyncSession, tenant_id: int, fmt: str, recipient_email: str,
    ) -> tuple[Task, UserExport]:
        """Enqueue an export_request task and its tracking row in one transaction."""
        task = Task(
            tenant_id=tenant_id, task_type="export_request", status="queued",
            payload={"format": fmt},
        )
        db.add(task)
        await db.flush()
        export = UserExport(
            tenant_id=tenant_id, task_id=task.id, format=fmt,
            status="pending", recipient_email=recipient_email,
        )
        db.add(export)
        await db.flush()
        await db.commit()
        return task, export

    @staticmethod
    async def has_pending_export(db: AsyncSession, tenant_id: int) -> bool:
        """True if the tenant already has a pending/processing export request."""
        result = await db.execute(
            select(UserExport.id).where(
                UserExport.tenant_id == tenant_id,
                UserExport.status.in_(["pending", "processing"]),
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def get_export_by_task_id(db: AsyncSession, task_id: int) -> UserExport | None:
        result = await db.execute(select(UserExport).where(UserExport.task_id == task_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def mark_export_processing(db: AsyncSession, export_id: int) -> None:
        await db.execute(
            update(UserExport).where(UserExport.id == export_id).values(status="processing")
        )
        await db.commit()

    @staticmethod
    async def mark_export_completed(
        db: AsyncSession, export_id: int, file_path: str, expires_at: datetime,
    ) -> None:
        await db.execute(
            update(UserExport).where(UserExport.id == export_id).values(
                status="completed",
                file_path=file_path,
                completed_at=datetime.now(timezone.utc),
                expires_at=expires_at,
            )
        )
        await db.commit()

    @staticmethod
    async def mark_export_failed(db: AsyncSession, export_id: int, error: str) -> None:
        await db.execute(
            update(UserExport).where(UserExport.id == export_id).values(
                status="failed", error_message=error,
            )
        )
        await db.commit()

    @staticmethod
    async def list_expired_exports(db: AsyncSession, now: datetime) -> list[UserExport]:
        """Completed exports past their retention window (admin session, no RLS)."""
        result = await db.execute(
            select(UserExport).where(
                UserExport.status == "completed",
                UserExport.expires_at.isnot(None),
                UserExport.expires_at < now,
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def delete_export_record(db: AsyncSession, export_id: int) -> None:
        from sqlalchemy import delete as sa_delete

        await db.execute(sa_delete(UserExport).where(UserExport.id == export_id))
        await db.commit()

    # --- Full-tenant data listing (user export — keyset-paginated) ---

    @staticmethod
    async def list_all_entities_for_tenant(
        db: AsyncSession, tenant_id: int, *, after_id: str | None = None, limit: int = 1000,
    ) -> list[Entity]:
        stmt = select(Entity).where(Entity.tenant_id == tenant_id)
        if after_id is not None:
            stmt = stmt.where(Entity.id > after_id)
        result = await db.execute(stmt.order_by(Entity.id.asc()).limit(limit))
        return list(result.scalars().all())

    @staticmethod
    async def list_all_metrics_for_tenant(
        db: AsyncSession, tenant_id: int, *, after_id: int | None = None, limit: int = 1000,
    ) -> list[EntityMetric]:
        stmt = select(EntityMetric).where(EntityMetric.tenant_id == tenant_id)
        if after_id is not None:
            stmt = stmt.where(EntityMetric.id > after_id)
        result = await db.execute(stmt.order_by(EntityMetric.id.asc()).limit(limit))
        return list(result.scalars().all())

    @staticmethod
    async def list_all_metric_history_for_tenant(
        db: AsyncSession, tenant_id: int, *, after_id: int | None = None, limit: int = 1000,
    ) -> list[EntityMetricHistory]:
        stmt = select(EntityMetricHistory).where(EntityMetricHistory.tenant_id == tenant_id)
        if after_id is not None:
            stmt = stmt.where(EntityMetricHistory.id > after_id)
        result = await db.execute(stmt.order_by(EntityMetricHistory.id.asc()).limit(limit))
        return list(result.scalars().all())

    @staticmethod
    async def list_all_signals_for_tenant(
        db: AsyncSession, tenant_id: int, *, after_id: str | None = None, limit: int = 1000,
    ) -> list[Signal]:
        stmt = select(Signal).where(Signal.tenant_id == tenant_id)
        if after_id is not None:
            stmt = stmt.where(Signal.id > after_id)
        result = await db.execute(stmt.order_by(Signal.id.asc()).limit(limit))
        return list(result.scalars().all())

    @staticmethod
    async def list_all_usage_records_for_tenant(
        db: AsyncSession, tenant_id: int, *, after_id: int | None = None, limit: int = 1000,
    ) -> list[UsageRecord]:
        stmt = select(UsageRecord).where(UsageRecord.tenant_id == tenant_id)
        if after_id is not None:
            stmt = stmt.where(UsageRecord.id > after_id)
        result = await db.execute(stmt.order_by(UsageRecord.id.asc()).limit(limit))
        return list(result.scalars().all())

    @staticmethod
    async def list_all_metering_records_for_tenant(
        db: AsyncSession, tenant_id: int, *, after_id: int | None = None, limit: int = 1000,
    ) -> list[MeteringRecord]:
        stmt = select(MeteringRecord).where(MeteringRecord.tenant_id == tenant_id)
        if after_id is not None:
            stmt = stmt.where(MeteringRecord.id > after_id)
        result = await db.execute(stmt.order_by(MeteringRecord.id.asc()).limit(limit))
        return list(result.scalars().all())

# ── helpers ────────────────────────────────────────────────────

def _build_embed_text_from_entity(entity: Entity, metrics: list[EntityMetric]) -> str:
    parts: list[str] = []
    if entity.free_text:
        parts.append(entity.free_text)
    if entity.fields:
        for k, v in entity.fields.items():
            if isinstance(v, (str, int, float)):
                parts.append(f"{k}: {v}")
    for m in metrics:
        parts.append(f"{m.metric_key}: {m.value:.2f}")
    return " ".join(parts)


def _build_embed_text_safe(
    entity: Entity, metrics: list[EntityMetric], pack_def: dict | None = None,
) -> str:
    """Build the embedding text, skipping sensitive metrics."""
    parts: list[str] = []
    if entity.free_text:
        parts.append(entity.free_text)
    if entity.fields:
        for k, v in entity.fields.items():
            if isinstance(v, (str, int, float)):
                parts.append(f"{k}: {v}")

    sensitive_keys: set[str] = set()
    if pack_def:
        sensitive_keys = set(pack_def.get("kvkk", {}).get("sensitive_metrics", []))

    for m in metrics:
        if m.metric_key in sensitive_keys:
            continue
        parts.append(f"{m.metric_key}: {m.value:.2f}")
    return " ".join(parts)


def _metric_row_to_read(row: EntityMetric) -> dict:
    from .decay import decayed_confidence

    d = {
        "metric_key": row.metric_key,
        "value": row.value,
        "confidence": row.confidence,
        "source_count": row.source_count,
        "last_updated": row.last_updated,
        "source_signal_id": row.signal_id,
        # Third fingerprint alongside prompt_hash/input_hash: the entity
        # context injected into the user message. Surfaced so a caller can
        # tell "the value moved because the entity context changed" apart
        # from model drift (see migration 021, replay.py).
        "context_hash": row.context_hash,
    }
    d["effective_confidence"] = decayed_confidence(row.confidence, row.last_updated)
    return d


# ── Encryption (Spec 025) ──────────────────────────────────────

_ENCRYPTION_KEY: bytes | None = None


def _get_encryption_key() -> bytes:
    global _ENCRYPTION_KEY
    if _ENCRYPTION_KEY is not None:
        return _ENCRYPTION_KEY
    key_env = config.HUMETRIC_ENCRYPTION_KEY
    if key_env:
        _ENCRYPTION_KEY = bytes.fromhex(key_env) if len(key_env) == 64 else key_env.encode().ljust(32, b"\x00")[:32]
    return _ENCRYPTION_KEY


def encrypt_key(plaintext: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = _get_encryption_key()
    if not key:
        raise RuntimeError("HUMETRIC_ENCRYPTION_KEY not configured")
    nonce = _os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + ciphertext).decode()


def decrypt_key(ciphertext_b64: str) -> str | None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = _get_encryption_key()
    if not key:
        return None
    try:
        raw = base64.b64decode(ciphertext_b64)
        nonce, ciphertext = raw[:12], raw[12:]
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(nonce, ciphertext, None).decode()
    except Exception:
        return None
