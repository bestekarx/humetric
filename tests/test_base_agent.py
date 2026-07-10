"""base.py retry/cache testleri — 0.3 bug fix."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import pytest
from pydantic import BaseModel

from humetric.agents.base import _get_client, structured_call


class FakeSchema(BaseModel):
    answer: str


@pytest.fixture(autouse=True)
def reset_client_cache():
    import humetric.agents.base as bm
    bm._client = None
    bm._byo_client_cache.clear()
    yield


@pytest.mark.asyncio
async def test_bad_request_raises_non_retryable():
    with patch("humetric.agents.base._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = anthropic.BadRequestError(
            message="bad request",
            response=MagicMock(status_code=400),
            body={"error": {"message": "bad"}},
        )
        mock_get_client.return_value = mock_client

        with pytest.raises(anthropic.BadRequestError):
            await structured_call(
                model="claude-haiku-4-5-20251001",
                system="test",
                user="test",
                schema=FakeSchema,
                tool_ad="test",
                tool_aciklama="test",
            )


@pytest.mark.asyncio
async def test_api_status_error_raises():
    with patch("humetric.agents.base._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = anthropic.APIStatusError(
            message="overloaded",
            response=MagicMock(status_code=529),
            body={"error": {"message": "overloaded"}},
        )
        mock_get_client.return_value = mock_client

        with pytest.raises(anthropic.APIStatusError):
            await structured_call(
                model="claude-haiku-4-5-20251001",
                system="test",
                user="test",
                schema=FakeSchema,
                tool_ad="test",
                tool_aciklama="test",
            )


@pytest.mark.asyncio
async def test_rate_limit_error_raises():
    with patch("humetric.agents.base._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = anthropic.RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429),
            body={"error": {"message": "rate limited"}},
        )
        mock_get_client.return_value = mock_client

        with pytest.raises(anthropic.RateLimitError):
            await structured_call(
                model="claude-haiku-4-5-20251001",
                system="test",
                user="test",
                schema=FakeSchema,
                tool_ad="test",
                tool_aciklama="test",
            )


def test_byo_client_cached():
    c1 = _get_client(api_key="sk-byo-key-1")
    c2 = _get_client(api_key="sk-byo-key-1")
    assert c1 is c2

    c3 = _get_client(api_key="sk-byo-key-2")
    assert c1 is not c3


def test_default_client_cached():
    import humetric.agents.base as bm
    bm._client = None
    c1 = _get_client()
    c2 = _get_client()
    assert c1 is c2
