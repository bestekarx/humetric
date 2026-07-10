"""KVKK uyumluluk testleri — pack-driven per-metric visible_to, consent, audit log.

Saha kvkk_check.py pattern'inden jeneriklestirilmistir.
Spec 023: per-metric `visible_to` listesi API key scope'lariyla kesisim kontrolu.
"""

from datetime import datetime, timedelta, timezone

import pytest

from humetric import kvkk
from humetric.store import Store


# ── Consent check tests ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_consent_check_granted(test_db, test_tenant):
    store = Store()

    await store.create_entity(test_db, {
        "id": "kvkk-entity",
        "tenant_id": test_tenant.id,
        "entity_type": "test_type",
    })

    await store.create_consent(test_db, {
        "tenant_id": test_tenant.id,
        "entity_id": "kvkk-entity",
        "scope": "hassas_veri_isleme",
        "status": "granted",
    })

    result = await kvkk.check_consent(
        test_db, "kvkk-entity", "hassas_veri_isleme", test_tenant.id
    )
    assert result is True


@pytest.mark.asyncio
async def test_consent_check_revoked(test_db, test_tenant):
    store = Store()

    await store.create_entity(test_db, {
        "id": "kvkk-revoked-entity",
        "tenant_id": test_tenant.id,
        "entity_type": "test_type",
    })

    await store.create_consent(test_db, {
        "tenant_id": test_tenant.id,
        "entity_id": "kvkk-revoked-entity",
        "scope": "hassas_veri_isleme",
        "status": "granted",
    })
    await store.revoke_consent(test_db, "kvkk-revoked-entity", "hassas_veri_isleme", test_tenant.id)

    result = await kvkk.check_consent(
        test_db, "kvkk-revoked-entity", "hassas_veri_isleme", test_tenant.id
    )
    assert result is False


@pytest.mark.asyncio
async def test_consent_check_expired(test_db, test_tenant):
    store = Store()

    await store.create_entity(test_db, {
        "id": "kvkk-expired-entity",
        "tenant_id": test_tenant.id,
        "entity_type": "test_type",
    })

    await store.create_consent(test_db, {
        "tenant_id": test_tenant.id,
        "entity_id": "kvkk-expired-entity",
        "scope": "saglik_verisi",
        "status": "granted",
        "expires_at": datetime.now(timezone.utc) - timedelta(days=1),
    })

    result = await kvkk.check_consent(
        test_db, "kvkk-expired-entity", "saglik_verisi", test_tenant.id
    )
    assert result is False


# ── Per-metric visible_to filter tests (Spec 023 pack-driven) ──

@pytest.mark.asyncio
async def test_sensitive_metric_not_visible_to_unauthorized_scope():
    metrics = [
        {"key": "satis_performansi", "value": 0.8, "confidence": 0.7},
        {"key": "mali_stres", "value": -0.5, "confidence": 0.6},
        {"key": "tahsilat_disiplini", "value": 0.3, "confidence": 0.5},
    ]

    pack = {
        "kvkk": {
            "sensitive_metrics": ["mali_stres"],
        },
        "metrics": [
            {"key": "satis_performansi", "sensitive": False},
            {"key": "mali_stres", "sensitive": True, "visible_to": ["entities:read", "entities:write"]},
            {"key": "tahsilat_disiplini", "sensitive": False},
        ],
    }

    # signals:write scope kesisim yok — mali_stres gorunmemeli
    operator_result = await kvkk.filter_sensitive_metrics(metrics, ["signals:write"], pack)
    assert len(operator_result) == 2
    keys = [m["key"] for m in operator_result]
    assert "mali_stres" not in keys


@pytest.mark.asyncio
async def test_sensitive_metric_visible_to_authorized_scope():
    metrics = [
        {"key": "satis_performansi", "value": 0.8, "confidence": 0.7},
        {"key": "mali_stres", "value": -0.5, "confidence": 0.6},
    ]

    pack = {
        "kvkk": {
            "sensitive_metrics": ["mali_stres"],
        },
        "metrics": [
            {"key": "satis_performansi", "sensitive": False},
            {"key": "mali_stres", "sensitive": True, "visible_to": ["entities:read", "entities:write"]},
        ],
    }

    # entities:read scope kesisim var — mali_stres gorunmeli
    result = await kvkk.filter_sensitive_metrics(metrics, ["entities:read"], pack)
    assert len(result) == 2
    keys = [m["key"] for m in result]
    assert "mali_stres" in keys


@pytest.mark.asyncio
async def test_visible_to_empty_shows_to_all():
    metrics = [
        {"key": "performans", "value": 0.8, "confidence": 0.7},
        {"key": "gizli_metrik", "value": 0.5, "confidence": 0.6},
    ]

    pack = {
        "kvkk": {
            "sensitive_metrics": ["gizli_metrik"],
        },
        "metrics": [
            {"key": "performans", "sensitive": False},
            {"key": "gizli_metrik", "sensitive": True, "visible_to": []},
        ],
    }

    # visible_to bossa her scope'tan gorunur
    result = await kvkk.filter_sensitive_metrics(metrics, ["signals:write"], pack)
    assert len(result) == 2
    keys = [m["key"] for m in result]
    assert "gizli_metrik" in keys


@pytest.mark.asyncio
async def test_filter_sensitive_metrics_no_pack():
    metrics = [{"key": "test", "value": 0.5, "confidence": 0.5}]
    result = await kvkk.filter_sensitive_metrics(metrics, [], None)
    assert len(result) == 1


@pytest.mark.asyncio
async def test_filter_sensitive_metrics_non_sensitive_always_visible():
    metrics = [
        {"key": "normal_metrik", "value": 0.9, "confidence": 0.8},
        {"key": "hassas_metrik", "value": 0.3, "confidence": 0.5},
    ]

    pack = {
        "kvkk": {
            "sensitive_metrics": ["hassas_metrik"],
        },
        "metrics": [
            {"key": "normal_metrik", "sensitive": False},
            {"key": "hassas_metrik", "sensitive": True, "visible_to": ["entities:read"]},
        ],
    }

    # sensitive olmayan metrik her zaman gorunur
    result = await kvkk.filter_sensitive_metrics(metrics, ["signals:write"], pack)
    assert len(result) == 1
    assert result[0]["key"] == "normal_metrik"


# ── Audit log test ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_audit_log(test_db, test_tenant):
    await kvkk.write_audit_log(
        test_db,
        action="kvkk_test",
        tenant_id=test_tenant.id,
        entity_id="test-entity",
        details={"test": True},
        api_key_id=1,
    )

    from sqlalchemy import select
    from humetric.db.models import AuditLog
    result = await test_db.execute(
        select(AuditLog).where(AuditLog.action == "kvkk_test")
    )
    logs = result.scalars().all()
    assert len(logs) >= 1
    assert logs[0].action == "kvkk_test"
