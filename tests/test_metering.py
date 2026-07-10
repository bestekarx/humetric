"""Metering testleri — Faz 1 sayac ve limit kontrolu."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from humetric.services.usage_service import (
    check_tier_limit,
    record_embedding,
    record_llm_tokens,
    record_signal,
)


@pytest.mark.asyncio
async def test_record_signal_increments_counter():
    calls = []

    async def fake_upsert_async(tenant_id, tarih, **fields):
        calls.append((tenant_id, tarih, fields))

    with patch(
        "humetric.services.usage_service._upsert_usage_async",
        side_effect=fake_upsert_async,
    ):
        await record_signal(1)
        await record_signal(1)

    assert len(calls) == 2
    assert calls[0][0] == 1
    assert calls[0][2].get("sinyal_sayisi") == 1


@pytest.mark.asyncio
async def test_record_llm_tokens_increments_counter():
    calls = []

    async def fake_upsert_async(tenant_id, tarih, **fields):
        calls.append((tenant_id, tarih, fields))

    with patch(
        "humetric.services.usage_service._upsert_usage_async",
        side_effect=fake_upsert_async,
    ):
        await record_llm_tokens(1, 500)

    assert len(calls) == 1
    assert calls[0][2].get("llm_token_sayisi") == 500


@pytest.mark.asyncio
async def test_record_embedding_increments_counter():
    calls = []

    async def fake_upsert_async(tenant_id, tarih, **fields):
        calls.append((tenant_id, tarih, fields))

    with patch(
        "humetric.services.usage_service._upsert_usage_async",
        side_effect=fake_upsert_async,
    ):
        await record_embedding(1)

    assert len(calls) == 1
    assert calls[0][2].get("embedding_sayisi") == 1


@pytest.mark.asyncio
async def test_check_tier_limit_free_below_limit():
    with patch(
        "humetric.services.usage_service.asyncio.to_thread",
        return_value=True,
    ):
        result = await check_tier_limit(1, "sinyal_sayisi", 500)
        assert result is True


@pytest.mark.asyncio
async def test_check_tier_limit_free_exceeds_limit():
    with patch(
        "humetric.services.usage_service.asyncio.to_thread",
        return_value=False,
    ):
        result = await check_tier_limit(1, "sinyal_sayisi", 2000)
        assert result is False


@pytest.mark.asyncio
async def test_check_tier_limit_pro_unlimited():
    with patch(
        "humetric.services.usage_service.asyncio.to_thread",
        return_value=True,
    ):
        result = await check_tier_limit(1, "sinyal_sayisi", 99999)
        assert result is True
