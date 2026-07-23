"""_persist_signal_result must accumulate source_count across signals for the
same metric_key instead of hardcoding 1 every time (regression: previously a
metric's source_count never advanced past 1 no matter how many signals fed
into it, silently breaking any confidence gate based on signal count, e.g.
"< 5 signals -> insufficient data")."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from humetric.schema import FinalMetric
from humetric.worker import _persist_signal_result


def _final_metric(metric_key: str, value: float = 0.5) -> FinalMetric:
    return FinalMetric(metric_key=metric_key, value=value, confidence=0.8)


def _existing_row(metric_key: str, source_count: int, value: float = 0.5) -> SimpleNamespace:
    # _build_embed_text_safe() also reads .value off each existing metric row.
    return SimpleNamespace(metric_key=metric_key, source_count=source_count, value=value)


def _entity(entity_id: str) -> SimpleNamespace:
    return SimpleNamespace(id=entity_id, free_text="", fields={})


@pytest.mark.asyncio
async def test_first_signal_writes_source_count_one():
    upserted: list[dict] = []

    async def fake_upsert_metric(db, data):
        upserted.append(data)
        return SimpleNamespace(**data)

    with patch("humetric.worker.Store.upsert_metric", side_effect=fake_upsert_metric), \
         patch("humetric.worker.Store.update_entity_embedding", new=AsyncMock()), \
         patch("humetric.worker.Store.update_signal_status", new=AsyncMock()):
        await _persist_signal_result(
            db=None,
            task=SimpleNamespace(tenant_id=1, signal_id="sig-1"),
            entity=_entity("rakip-1"),
            extracted=[],
            final_metrics=[_final_metric("dijital_itibar")],
            extract_meta={},
            curator_meta={},
            existing_metrics=[],
            pack_def={},
            input_hash="h1",
        )

    assert upserted[0]["source_count"] == 1


@pytest.mark.asyncio
async def test_second_signal_for_same_metric_increments_source_count():
    upserted: list[dict] = []

    async def fake_upsert_metric(db, data):
        upserted.append(data)
        return SimpleNamespace(**data)

    with patch("humetric.worker.Store.upsert_metric", side_effect=fake_upsert_metric), \
         patch("humetric.worker.Store.update_entity_embedding", new=AsyncMock()), \
         patch("humetric.worker.Store.update_signal_status", new=AsyncMock()):
        await _persist_signal_result(
            db=None,
            task=SimpleNamespace(tenant_id=1, signal_id="sig-2"),
            entity=_entity("rakip-1"),
            extracted=[],
            final_metrics=[_final_metric("dijital_itibar", value=0.6)],
            extract_meta={},
            curator_meta={},
            # Second signal for an entity that already has one prior
            # observation of this exact metric_key.
            existing_metrics=[_existing_row("dijital_itibar", source_count=1)],
            pack_def={},
            input_hash="h2",
        )

    assert upserted[0]["source_count"] == 2


@pytest.mark.asyncio
async def test_new_metric_key_on_entity_with_history_still_starts_at_one():
    """A metric_key with no prior row of its own starts at 1, even if the
    entity already has history for *other* metric_keys."""
    upserted: list[dict] = []

    async def fake_upsert_metric(db, data):
        upserted.append(data)
        return SimpleNamespace(**data)

    with patch("humetric.worker.Store.upsert_metric", side_effect=fake_upsert_metric), \
         patch("humetric.worker.Store.update_entity_embedding", new=AsyncMock()), \
         patch("humetric.worker.Store.update_signal_status", new=AsyncMock()):
        await _persist_signal_result(
            db=None,
            task=SimpleNamespace(tenant_id=1, signal_id="sig-3"),
            entity=_entity("rakip-1"),
            extracted=[],
            final_metrics=[_final_metric("fiyat_pozisyonlama")],
            extract_meta={},
            curator_meta={},
            existing_metrics=[_existing_row("dijital_itibar", source_count=4)],
            pack_def={},
            input_hash="h3",
        )

    assert upserted[0]["source_count"] == 1
