"""KVKK/GDPR uyumluluk kontrolleri — consent, hassas metrik filtreleme, audit log.

Saha kvkk_check.py pattern'inden uyarlanmistir.
Spec 023: pack-driven per-metric visible_to filtreleme tamamlandi.
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
    """Entity'nin belirtilen kapsamda gecerli consent'i var mi?

    Granted status + expires_at gecmemis olmali.
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
    """Metric'in requires_consent_scope'u icin gecerli consent var mi?"""
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
    """Hassas metrikleri consent + API key scope'larina gore filtrele.

    KVKK okuma kapisi (consent okuma gorunurlugune baglidir):
    Hassas bir metrik (kvkk.sensitive_metrics) icin:
      - Metrigin `requires_consent_scope`'u icin gecerli consent VARSA → gorunur
        (consent okuma iznini saglar; visible_to/scope kontrolu gevsetilir).
      - Consent YOKSA → `visible_to` scope kapisina dusulur: API key'in
        scope'lari visible_to ile kesisiyorsa gorunur (or. admin), yoksa gizlenir.
    Consent kaldirilinca metrik okumada da gizlenir.

    db/entity_id/tenant_id verilmezse consent kontrolu yapilamaz; bu durumda
    yalnizca visible_to/scope kapisi uygulanir (geriye donuk uyumlu).
    visible_to bossa ve consent gerekmiyorsa → herkes gorur.
    pack yoksa → tum metrikler gorunur (filtre uygulanmaz).
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
                if allowed_scopes and not (set(scopes) & set(allowed_scopes)):
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
    """Denetim kaydi yaz."""
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
