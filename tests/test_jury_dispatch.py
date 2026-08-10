"""structured_call_jury dispatch tests — no live LLM.

Covers the behaviour that only shows up once several providers are active:
parallel fan-out, partial-failure tolerance, and trace metadata naming the
provider that actually decided.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

from humetric.agents.multi_llm import structured_call_jury


class Out(BaseModel):
    label: str
    score: float


def model_for(provider: str) -> str:
    return f"{provider}-model"


async def call(members, **kw):
    return await structured_call_jury(
        members=members,
        system="s",
        user="u",
        schema=Out,
        tool_name="t",
        tool_description="d",
        model_for=model_for,
        **kw,
    )


# ── single member ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_member_skips_the_jury():
    """One provider must behave exactly like the old single-provider path."""
    with patch(
        "humetric.agents.multi_llm.structured_call_multi",
        AsyncMock(return_value=Out(label="x", score=0.5)),
    ) as mock:
        meta: dict = {}
        result = await call([("anthropic", "k")], call_meta=meta)

    assert mock.await_count == 1
    assert result.score == 0.5
    assert mock.await_args.kwargs["model"] == "anthropic-model"
    assert meta["provider"] == "anthropic"
    assert "jury" not in meta


@pytest.mark.asyncio
async def test_no_members_is_an_error():
    with pytest.raises(RuntimeError):
        await call([])


# ── jury mode ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_member_is_called_once():
    async def fake(*, provider, **kw):
        return Out(label="x", score=0.5)

    with patch("humetric.agents.multi_llm.structured_call_multi", AsyncMock(side_effect=fake)) as mock:
        await call([("anthropic", "k1"), ("openai", "k2"), ("google", "k3")])

    assert mock.await_count == 3
    assert {c.kwargs["provider"] for c in mock.await_args_list} == {"anthropic", "openai", "google"}


@pytest.mark.asyncio
async def test_members_run_in_parallel():
    """Total time should track the slowest member, not their sum."""
    async def slow(**kw):
        await asyncio.sleep(0.1)
        return Out(label="x", score=0.5)

    with patch("humetric.agents.multi_llm.structured_call_multi", AsyncMock(side_effect=slow)):
        started = asyncio.get_event_loop().time()
        await call([("anthropic", "k"), ("openai", "k"), ("google", "k")])
        elapsed = asyncio.get_event_loop().time() - started

    assert elapsed < 0.25, f"not parallel, took {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_a_failing_member_does_not_sink_the_call():
    """A rate-limited provider must not take down a signal the others answered."""
    async def fake(*, provider, **kw):
        if provider == "openai":
            raise RuntimeError("429 rate limited")
        return Out(label="ok", score=0.8)

    with patch("humetric.agents.multi_llm.structured_call_multi", AsyncMock(side_effect=fake)):
        meta: dict = {}
        result = await call([("anthropic", "k"), ("openai", "k")], call_meta=meta)

    assert result.score == 0.8
    assert meta["jury"]["ok_count"] == 1
    assert any(m["error"] for m in meta["jury"]["members"])


@pytest.mark.asyncio
async def test_all_members_failing_raises():
    with patch(
        "humetric.agents.multi_llm.structured_call_multi",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        with pytest.raises(RuntimeError, match="All jury members failed"):
            await call([("anthropic", "k"), ("openai", "k")])


@pytest.mark.asyncio
async def test_trace_records_the_deciding_provider():
    async def fake(*, provider, **kw):
        return Out(label="x", score=0.8 if provider == "anthropic" else 0.79)

    with patch("humetric.agents.multi_llm.structured_call_multi", AsyncMock(side_effect=fake)):
        meta: dict = {}
        await call([("anthropic", "k"), ("openai", "k")], call_meta=meta)

    assert meta["provider"] in ("anthropic", "openai")
    assert meta["model"] == f"{meta['provider']}-model"
    assert meta["jury"]["member_count"] == 2
    assert meta["jury"]["agreement"] == pytest.approx(1.0)  # within tolerance


@pytest.mark.asyncio
async def test_each_member_gets_its_own_key():
    seen: dict[str, str | None] = {}

    async def fake(*, provider, api_key, **kw):
        seen[provider] = api_key
        return Out(label="x", score=0.5)

    with patch("humetric.agents.multi_llm.structured_call_multi", AsyncMock(side_effect=fake)):
        await call([("anthropic", "key-a"), ("openai", "key-o")])

    assert seen == {"anthropic": "key-a", "openai": "key-o"}
