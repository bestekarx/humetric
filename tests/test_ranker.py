"""Ranker entity_type testleri — 0.2 bug fix: sonuclar dogru entity_type tasir."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from humetric.agents.ranker import rank_entities
from humetric.db.models import Entity
from humetric.schema import RankingResult, RankedResultLLM


def _make_entity(eid: str, entity_type: str) -> Entity:
    return Entity(
        id=eid,
        tenant_id=1,
        entity_type=entity_type,
        fields={},
        free_text="",
        status="active",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_ranked_results_carry_correct_entity_type():
    entities = [
        _make_entity("e1", "personel"),
        _make_entity("e2", "sirket"),
        _make_entity("e3", "personel"),
    ]

    mock_llm_response = RankingResult(results=[
        RankedResultLLM(entity_id="e1", score=0.9, reasoning="relevant"),
        RankedResultLLM(entity_id="e2", score=0.7, reasoning="somewhat"),
        RankedResultLLM(entity_id="e3", score=0.5, reasoning="less"),
    ])

    with patch(
        "humetric.agents.ranker.structured_call",
        new_callable=AsyncMock,
        return_value=mock_llm_response,
    ):
        results = await rank_entities(entities, query="test", top_k=10)

    assert len(results) == 3
    assert results[0].entity_type == "personel"
    assert results[1].entity_type == "sirket"
    assert results[2].entity_type == "personel"


@pytest.mark.asyncio
async def test_unknown_entity_id_gets_empty_type():
    entities = [_make_entity("e1", "personel")]

    mock_llm_response = RankingResult(results=[
        RankedResultLLM(entity_id="unknown_id", score=0.5, reasoning=""),
    ])

    with patch(
        "humetric.agents.ranker.structured_call",
        new_callable=AsyncMock,
        return_value=mock_llm_response,
    ):
        results = await rank_entities(entities, query="test", top_k=10)

    assert results[0].entity_type == ""


@pytest.mark.asyncio
async def test_empty_input_returns_empty_list():
    results = await rank_entities([], query="test")
    assert results == []
