"""Embedding provider testleri — Spec 024: Voyage + OpenAI + Cohere."""

import os
from unittest.mock import MagicMock, patch

import pytest

from humetric.embeddings import (
    CohereEmbeddingProvider,
    EmbeddingProvider,
    OpenAIEmbeddingProvider,
    VoyageEmbeddingProvider,
    get_embedding_provider,
)


class FakeEmbeddingProvider(EmbeddingProvider):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 1024 for _ in texts]


@pytest.mark.asyncio
async def test_embedding_provider_abc():
    """ABC dogru tanimlanmis mi?"""
    provider = FakeEmbeddingProvider()
    result = await provider.embed(["test"])
    assert len(result) == 1
    assert len(result[0]) == 1024


@pytest.mark.asyncio
async def test_voyage_provider_embed_skip_if_no_key():
    """Voyage key yoksa atlanir."""
    api_key = os.environ.get("VOYAGE_API_KEY", "")
    if not api_key or api_key == "test-key":
        pytest.skip("VOYAGE_API_KEY not set (test key, skipping live test)")

    provider = VoyageEmbeddingProvider(api_key=api_key, dimensions=1024)
    result = await provider.embed(["hello world"])
    assert len(result) == 1
    assert len(result[0]) == 1024


@pytest.mark.asyncio
async def test_voyage_provider_empty_list():
    """Bos liste islenmez."""
    provider = VoyageEmbeddingProvider(api_key="test-key", dimensions=1024)
    result = await provider.embed([])
    assert result == []


@pytest.mark.asyncio
async def test_get_embedding_provider_defaults_to_voyage():
    provider = get_embedding_provider()
    assert isinstance(provider, VoyageEmbeddingProvider)


@pytest.mark.asyncio
async def test_openai_provider_embed():
    """US4: OpenAIEmbeddingProvider dogru boyutta embedding dondurur."""
    with patch("humetric.embeddings.openai") as mock_openai:
        mock_client = MagicMock()
        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1536
        mock_client.embeddings.create.return_value = type("Response", (), {"data": [mock_embedding]})()
        mock_openai.OpenAI.return_value = mock_client

        provider = OpenAIEmbeddingProvider(api_key="test-key", dimensions=1536)
        result = await provider.embed(["test text"])
        assert len(result) == 1
        assert len(result[0]) == 1536


@pytest.mark.asyncio
async def test_openai_provider_empty_list():
    """US4: OpenAI bos liste islenmez."""
    provider = OpenAIEmbeddingProvider(api_key="test-key")
    result = await provider.embed([])
    assert result == []


@pytest.mark.asyncio
async def test_cohere_provider_embed():
    """US4: CohereEmbeddingProvider dogru boyutta embedding dondurur."""
    with patch("humetric.embeddings.cohere") as mock_cohere:
        mock_client = MagicMock()
        mock_client.embed.return_value = type("Response", (), {
            "embeddings": [[0.1] * 1024]
        })()
        mock_cohere.ClientV2.return_value = mock_client

        provider = CohereEmbeddingProvider(api_key="test-key", dimensions=1024)
        result = await provider.embed(["test text"])
        assert len(result) == 1
        assert len(result[0]) == 1024


@pytest.mark.asyncio
async def test_cohere_provider_empty_list():
    """US4: Cohere bos liste islenmez."""
    provider = CohereEmbeddingProvider(api_key="test-key")
    result = await provider.embed([])
    assert result == []


@pytest.mark.asyncio
async def test_embedding_provider_retryable_detection():
    """US3: _is_retryable 4xx → false, 5xx → true."""
    provider = VoyageEmbeddingProvider(api_key="test-key")

    class HTTPError(Exception):
        def __init__(self, status_code):
            self.status_code = status_code

    assert not provider._is_retryable(HTTPError(400))
    assert not provider._is_retryable(HTTPError(404))
    assert provider._is_retryable(HTTPError(500))
    assert provider._is_retryable(HTTPError(429))
    assert provider._is_retryable(Exception("Unknown error"))
