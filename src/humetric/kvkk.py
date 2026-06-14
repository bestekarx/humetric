"""KVKK/GDPR compliance checks — consent, sensitive metric filtering, audit log.

Spec 023: pack-driven per-metric visible_to filtering completed.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from .store import Store

_log = logging.getLogger(__name__)


async def check_consent(
    db: AsyncSession, entity_id: str, scope: str, tenant_id: int,
) -> bool:
    """Does the entity have valid consent in the given scope?

    Status must be granted and expires_at must not be in the past.
    """
    from .db.models import Consent
    from sqlalchemy import select

    result = await db.execute(
        select(Consent).where(
            Consent.entity_id == entity_id,
            Consent.scope == scope,
            Consent.tenant_id == tenant_id,
            Consent.status == "granted",
        )
    )
    consent = result.scalar_one_or_none()
    if not consent:
        return False
    if consent.expires_at and consent.expires_at < datetime.now(timezone.utc):
        return False
    return True


async def check_consent_for_metric(
    db: AsyncSession, entity_id: str, requires_consent_scope: str, tenant_id: int,
) -> bool:
    """Is there valid consent for the metric's requires_consent_scope?"""
    if not requires_consent_scope:
        return True
    return await check_consent(db, entity_id, requires_consent_scope, tenant_id)


async def filter_sensitive_metrics(
    metrics: list[dict],
    scopes: list[str],
    pack: dict | None = None,
    db: AsyncSession | None = None,
    entity_id: str | None = None,
    tenant_id: int | None = None,
) -> list[dict]:
    """Filter sensitive metrics based on consent + API key scopes.

    KVKK read gate (depends on consent-driven visibility):
    For a sensitive metric (kvkk.sensitive_metrics):
      - If valid consent EXISTS for the metric's `requires_consent_scope` → visible
        (consent grants read access; the visible_to/scope check is relaxed).
      - If consent is ABSENT → falls back to the `visible_to` scope gate: visible
        if the API key's scopes intersect visible_to (e.g. admin), hidden otherwise.
    Revoking consent immediately hides the metric from reads again.

    Without db/entity_id/tenant_id, consent cannot be checked; in that case
    only the visible_to/scope gate is applied (backward compatible).
    If visible_to is empty and consent isn't required → everyone sees it.
    Without a pack → all metrics are visible (no filtering applied).
    """
    if not pack:
        return metrics

    metrics_list = pack.get("metrics", [])
    if not metrics_list:
        return metrics

    metric_visible_map: dict[str, list[str]] = {}
    metric_consent_scope: dict[str, str | None] = {}
    for m in metrics_list:
        key = m.get("key", "")
        metric_visible_map[key] = m.get("visible_to", [])
        metric_consent_scope[key] = m.get("requires_consent_scope")

    sensitive_keys = set(pack.get("kvkk", {}).get("sensitive_metrics", []))

    filtered = []
    for m in metrics:
        key = m.get("key", m.get("metric_key", ""))
        if key in sensitive_keys:
            consent_scope = metric_consent_scope.get(key)
            has_consent = False
            if consent_scope and db is not None and entity_id and tenant_id is not None:
                has_consent = await check_consent(db, entity_id, consent_scope, tenant_id)

            if not has_consent:
                allowed_scopes = metric_visible_map.get(key, [])
                if consent_scope:
                    # Consent is required but absent: hidden by default, unless
                    # the caller's scopes are explicitly listed in visible_to.
                    if not (set(scopes) & set(allowed_scopes)):
                        continue
                elif allowed_scopes and not (set(scopes) & set(allowed_scopes)):
                    continue
        filtered.append(m)
    return filtered


async def write_audit_log(
    db: AsyncSession,
    action: str,
    tenant_id: int,
    entity_id: str | None = None,
    details: dict | None = None,
    api_key_id: int | None = None,
) -> None:
    """Write an audit log entry."""
    await Store.write_audit_log(
        db,
        {
            "tenant_id": tenant_id,
            "action": action,
            "entity_id": entity_id,
            "details": details,
            "api_key_id": api_key_id,
        },
    )
