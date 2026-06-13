"""Data access layer — async SQLAlchemy operations.

CRUD operations for Entity, Tenant, ApiKey, Consent, AuditLog, Signal.
All operations run on an async session.
"""

from __future__ import annotations

import base64
import logging
import os as _os
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import auth, config
from .db.models import ApiKey, AuditLog, Consent, Entity, EntityMetric, MetricPack, Signal, Task, Tenant, UsageRecord

_log = logging.getLogger(__name__)


class Store:
    """Async data access layer."""

    # --- Tenant ---

    @staticmethod
    async def create_tenant(db: AsyncSession, data: dict) -> Tenant:
        tenant = Tenant(**data)
        db.add(tenant)
        await db.commit()
        await db.refresh(tenant)
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
        await db.commit()
        await db.refresh(entity)
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
        await db.commit()
        await db.refresh(entity)
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
    async def upsert_metric(db: AsyncSession, data: dict) -> EntityMetric:
        entity_id = data["entity_id"]
        metric_key = data["metric_key"]

        existing = await db.execute(
            select(EntityMetric).where(
                EntityMetric.entity_id == entity_id,
                EntityMetric.metric_key == metric_key,
            )
        )
        metric = existing.scalar_one_or_none()

        if metric:
            for key, value in data.items():
                if key not in ("id", "entity_id", "metric_key", "created_at"):
                    setattr(metric, key, value)
            metric.last_updated = datetime.now(timezone.utc)
        else:
            metric = EntityMetric(**data)

        db.add(metric)
        await db.commit()
        await db.refresh(metric)
        return metric

    # --- Signal (Spec 022) ---

    @staticmethod
    async def create_signal(db: AsyncSession, data: dict) -> Signal:
        signal = Signal(**data)
        db.add(signal)
        await db.commit()
        await db.refresh(signal)
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
    async def update_signal_status(
        db: AsyncSession, signal_id: str, tenant_id: int,
        status: str, result: dict | None = None, error: str | None = None,
    ) -> Signal | None:
        signal = await Store.get_signal(db, signal_id, tenant_id)
        if not signal:
            return None
        signal.status = status
        if result:
            signal.result = {**signal.result, **result}
        if error:
            signal.error = error
        if status in ("completed", "failed"):
            signal.processed_at = datetime.now(timezone.utc)
        db.add(signal)
        await db.commit()
        await db.refresh(signal)
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
        await db.commit()
        await db.refresh(api_key)
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
    async def revoke_api_key(db: AsyncSession, key_id: int) -> bool:
        result = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
        api_key = result.scalar_one_or_none()
        if api_key is None:
            return False
        api_key.is_revoked = True
        await db.commit()
        return True

    @staticmethod
    async def list_api_keys(
        db: AsyncSession, tenant_id: int,
    ) -> list[ApiKey]:
        result = await db.execute(
            select(ApiKey).where(ApiKey.tenant_id == tenant_id)
        )
        return list(result.scalars().all())

    # --- Consent ---

    @staticmethod
    async def create_consent(db: AsyncSession, data: dict) -> Consent:
        consent = Consent(**data)
        db.add(consent)
        await db.commit()
        await db.refresh(consent)
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
        await db.commit()
        await db.refresh(log)
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
        await db.commit()
        await db.refresh(pack)
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
        await db.commit()
        await db.refresh(pack)
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
            # pgvector tipinin kendi operatorunu kullan: list -> vector
            # adaptasyonunu dogru yapar (func.cosine_distance ham list'i
            # asyncpg'ye str bekleyerek gonderir ve patlar).
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
        await db.commit()
        await db.refresh(task)
        return task

    @staticmethod
    async def get_next_task(
        db: AsyncSession, batch_size: int = 5,
    ) -> list[Task]:
        """Pull tasks from the queue with SELECT ... FOR UPDATE SKIP LOCKED."""
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(Task)
            .where(
                Task.status == "queued",
                (Task.next_retry_at.is_(None)) | (Task.next_retry_at <= now),
            )
            .order_by(Task.created_at.asc())
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
        return result.scalar_one_or_none()

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
    async def get_tenant_keys(db: AsyncSession, tenant_id: int) -> dict:
        tenant = await Store.get_tenant_by_id(db, tenant_id)
        if not tenant:
            return {"has_anthropic_key": False, "has_voyage_key": False, "updated_at": None}
        return {
            "has_anthropic_key": bool(tenant.anthropic_key_encrypted),
            "has_voyage_key": bool(tenant.voyage_key_encrypted),
            "updated_at": tenant.updated_at,
        }

    @staticmethod
    async def upsert_tenant_keys(
        db: AsyncSession, tenant_id: int, data: dict,
    ) -> dict:
        tenant = await Store.get_tenant_by_id(db, tenant_id)
        if not tenant:
            return {"has_anthropic_key": False, "has_voyage_key": False, "updated_at": None}
        if data.get("anthropic_key") is not None:
            tenant.anthropic_key_encrypted = encrypt_key(data["anthropic_key"])
        if data.get("voyage_key") is not None:
            tenant.voyage_key_encrypted = encrypt_key(data["voyage_key"])
        tenant.updated_at = datetime.now(timezone.utc)
        db.add(tenant)
        await db.commit()
        return {
            "has_anthropic_key": bool(tenant.anthropic_key_encrypted),
            "has_voyage_key": bool(tenant.voyage_key_encrypted),
            "updated_at": tenant.updated_at,
        }

    @staticmethod
    async def delete_tenant_keys(db: AsyncSession, tenant_id: int) -> dict:
        tenant = await Store.get_tenant_by_id(db, tenant_id)
        if not tenant:
            return {"has_anthropic_key": False, "has_voyage_key": False, "updated_at": None}
        tenant.anthropic_key_encrypted = None
        tenant.voyage_key_encrypted = None
        tenant.updated_at = None
        db.add(tenant)
        await db.commit()
        return {"has_anthropic_key": False, "has_voyage_key": False, "updated_at": None}

    @staticmethod
    async def decrypt_tenant_key(
        db: AsyncSession, tenant_id: int, key_type: str,
    ) -> str | None:
        tenant = await Store.get_tenant_by_id(db, tenant_id)
        if not tenant:
            return None
        encrypted = (
            tenant.anthropic_key_encrypted if key_type == "anthropic"
            else tenant.voyage_key_encrypted
        )
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
        await db.commit()
        await db.refresh(metric)
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
