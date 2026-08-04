"""GET /v1/entities/{id}/metrics/{key}/explain testleri.

trace_data.extracted[].reasoning + .source_span DB'de yazılı ama hiçbir endpoint
döndürmüyordu (K3 -> explain endpoint). Diğer worker testleri gibi FastAPI route
fonksiyonu doğrudan çağrılır, Store metotları patch'lenir (ASGI client'a gerek yok).
"""

from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from humetric.api import explain_metric


def _now():
    return datetime.now(timezone.utc)


def _request(scopes=None):
    scopes = scopes if scopes is not None else ["entities:read"]
    return SimpleNamespace(state=SimpleNamespace(tenant_id=1, scopes=scopes))


def _entity(entity_id="bayi-1", entity_type="bayi"):
    return SimpleNamespace(id=entity_id, entity_type=entity_type)


def _pack(entity_type="bayi", sensitive_metrics=None):
    return SimpleNamespace(definition={
        "entity_type": entity_type,
        "metrics": [
            {"key": "tahsilat_disiplini", "sensitive": False, "visible_to": []},
            {"key": "gizli_metrik", "sensitive": True, "visible_to": ["admin"],
             "requires_consent_scope": None},
        ],
        "kvkk": {"sensitive_metrics": sensitive_metrics or []},
    })


def _metric_row(metric_key="tahsilat_disiplini", trace_data=None):
    return SimpleNamespace(
        metric_key=metric_key,
        value=0.42,
        confidence=0.8,
        source_count=3,
        last_updated=_now(),
        signal_id="sig-1",
        trace_data=trace_data or {},
    )


@pytest.mark.asyncio
async def test_explain_metric_returns_reasoning_and_source_span():
    trace = {
        "extracted": [
            {
                "metric_key": "tahsilat_disiplini",
                "value": 0.42,
                "confidence": 0.8,
                "reasoning": "Vadesi geçen tutar bakiyenin %30'u, ödeme sözü verdi.",
                "source_span": "önceki borcunu kapattığını hatırlattı",
                "needs_review": False,
            }
        ],
        "extract_model": "claude-haiku",
        "curator_model": "claude-sonnet",
        "needs_review": False,
    }

    with patch("humetric.api.Store.get_entity", new=AsyncMock(return_value=_entity())), \
         patch("humetric.api.Store.get_active_pack_for_type", new=AsyncMock(return_value=_pack())), \
         patch("humetric.api.Store.get_metric_with_trace",
               new=AsyncMock(return_value=_metric_row(trace_data=trace))):
        result = await explain_metric(
            entity_id="bayi-1", metric_key="tahsilat_disiplini",
            request=_request(), db=None,
        )

    assert result.metric_key == "tahsilat_disiplini"
    assert result.value == 0.42
    assert result.source_count == 3
    assert result.extract_model == "claude-haiku"
    assert result.curator_model == "claude-sonnet"
    assert len(result.extracted) == 1
    assert result.extracted[0].reasoning == "Vadesi geçen tutar bakiyenin %30'u, ödeme sözü verdi."
    assert result.extracted[0].source_span == "önceki borcunu kapattığını hatırlattı"


@pytest.mark.asyncio
async def test_explain_metric_entity_not_found_404():
    with patch("humetric.api.Store.get_entity", new=AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await explain_metric(
                entity_id="yok", metric_key="tahsilat_disiplini",
                request=_request(), db=None,
            )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_explain_metric_metric_not_found_404():
    with patch("humetric.api.Store.get_entity", new=AsyncMock(return_value=_entity())), \
         patch("humetric.api.Store.get_active_pack_for_type", new=AsyncMock(return_value=_pack())), \
         patch("humetric.api.Store.get_metric_with_trace", new=AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await explain_metric(
                entity_id="bayi-1", metric_key="olmayan_metrik",
                request=_request(), db=None,
            )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_explain_metric_sensitive_metric_hidden_returns_404_not_403():
    """Hassas ve visible_to gate'ini geçemeyen metrik için 404 dönmeli (403 değil) —
    varlığının sızmaması gerekiyor."""
    with patch("humetric.api.Store.get_entity", new=AsyncMock(return_value=_entity())), \
         patch("humetric.api.Store.get_active_pack_for_type",
               new=AsyncMock(return_value=_pack(sensitive_metrics=["gizli_metrik"]))), \
         patch("humetric.api.Store.get_metric_with_trace",
               new=AsyncMock(return_value=_metric_row(metric_key="gizli_metrik"))):
        with pytest.raises(HTTPException) as exc_info:
            await explain_metric(
                entity_id="bayi-1", metric_key="gizli_metrik",
                request=_request(scopes=["entities:read"]), db=None,
            )
    assert exc_info.value.status_code == 404
