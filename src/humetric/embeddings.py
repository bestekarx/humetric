"""Embedding saglayici soyutlamasi — ABC + Voyage/OpenAI/Cohere implementasyonu (Spec 024).

Voyage free tier 3 RPM icin built-in backoff (22s, 6 retry).
Config'den saglayici degistirilebilir; tenant override desteklenir.
"""

from __future__ import annotations

import asyncio
import logging
import os
from abc import ABC, abstractmethod

_log = logging.getLogger(__name__)


class EmbeddingProvider(ABC):
    """Soyut embedding saglayici."""

    def __init__(self, dimensions: int, backoff_s: float = 22.0, max_retries: int = 6):
        self.dimensions = dimensions
        self.backoff_s = backoff_s
        self.max_retries = max_retries

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Metin listesini embedding vektorlerine donustur."""
        ...

    def _is_retryable(self, exc: Exception) -> bool:
        """5xx/429 → retryable, 4xx → immediate fail."""
        status = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
        if status and 400 <= status < 500:
            return False
        if status is None:
            msg = str(exc).lower()
            if "bad request" in msg or "invalid" in msg or "not found" in msg:
                return False
        return True


class VoyageEmbeddingProvider(EmbeddingProvider):
    """Voyage AI embedding saglayicisi."""

    def __init__(
        self,
        api_key: str,
        model: str = "voyage-3",
        dimensions: int = 1024,
        backoff_s: float = 22.0,
        max_retries: int = 6,
    ):
        super().__init__(dimensions=dimensions, backoff_s=backoff_s, max_retries=max_retries)
        self.api_key = api_key
        self.model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            import voyageai
            # voyageai>=0.2: embed() Client uzerindedir, modul seviyesinde degil.
            self._client = voyageai.Client(api_key=self.api_key)
        return self._client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        client = self._get_client()
        last_exc = None

        for attempt in range(self.max_retries + 1):
            try:
                result = await asyncio.to_thread(
                    lambda: client.embed(
                        texts,
                        model=self.model,
                        input_type="document",
                    )
                )
                return [list(v) for v in result.embeddings]
            except Exception as exc:
                last_exc = exc
                if not self._is_retryable(exc):
                    raise RuntimeError(f"Voyage embedding non-retryable error: {exc}") from exc
                if attempt < self.max_retries:
                    wait = self.backoff_s * (2 ** attempt)
                    _log.warning(
                        "Voyage embed failed (attempt %d/%d), retrying in %.0fs: %s",
                        attempt + 1, self.max_retries + 1, wait, exc,
                    )
                    await asyncio.sleep(wait)
                else:
                    _log.error("Voyage embed failed after %d retries: %s", self.max_retries + 1, exc)

        raise RuntimeError(
            f"Voyage embedding failed after {self.max_retries + 1} attempts"
        ) from last_exc


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI embedding saglayicisi — text-embedding-3-small, 1536 dim."""

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
        dimensions: int = 1536,
        backoff_s: float = 22.0,
        max_retries: int = 6,
    ):
        super().__init__(dimensions=dimensions, backoff_s=backoff_s, max_retries=max_retries)
        self.api_key = api_key
        self.model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            import openai
            self._client = openai.OpenAI(api_key=self.api_key)
        return self._client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        client = self._get_client()
        last_exc = None

        for attempt in range(self.max_retries + 1):
            try:
                result = await asyncio.to_thread(
                    lambda: client.embeddings.create(
                        model=self.model,
                        input=texts,
                        dimensions=self.dimensions,
                    )
                )
                return [list(d.embedding) for d in result.data]
            except Exception as exc:
                last_exc = exc
                if not self._is_retryable(exc):
                    raise RuntimeError(f"OpenAI embedding non-retryable error: {exc}") from exc
                if attempt < self.max_retries:
                    wait = self.backoff_s * (2 ** attempt)
                    _log.warning(
                        "OpenAI embed failed (attempt %d/%d), retrying in %.0fs: %s",
                        attempt + 1, self.max_retries + 1, wait, exc,
                    )
                    await asyncio.sleep(wait)
                else:
                    _log.error("OpenAI embed failed after %d retries: %s", self.max_retries + 1, exc)

        raise RuntimeError(
            f"OpenAI embedding failed after {self.max_retries + 1} attempts"
        ) from last_exc


class CohereEmbeddingProvider(EmbeddingProvider):
    """Cohere embedding saglayicisi — embed-english-v3.0, 1024 dim."""

    def __init__(
        self,
        api_key: str,
        model: str = "embed-english-v3.0",
        dimensions: int = 1024,
        backoff_s: float = 22.0,
        max_retries: int = 6,
    ):
        super().__init__(dimensions=dimensions, backoff_s=backoff_s, max_retries=max_retries)
        self.api_key = api_key
        self.model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            import cohere
            self._client = cohere.ClientV2(api_key=self.api_key)
        return self._client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        client = self._get_client()
        last_exc = None

        for attempt in range(self.max_retries + 1):
            try:
                result = await asyncio.to_thread(
                    lambda: client.embed(
                        model=self.model,
                        texts=texts,
                        input_type="classification",
                    )
                )
                return [list(e) if hasattr(e, '__iter__') else [float(e)] for e in result.embeddings]
            except Exception as exc:
                last_exc = exc
                if not self._is_retryable(exc):
                    raise RuntimeError(f"Cohere embedding non-retryable error: {exc}") from exc
                if attempt < self.max_retries:
                    wait = self.backoff_s * (2 ** attempt)
                    _log.warning(
                        "Cohere embed failed (attempt %d/%d), retrying in %.0fs: %s",
                        attempt + 1, self.max_retries + 1, wait, exc,
                    )
                    await asyncio.sleep(wait)
                else:
                    _log.error("Cohere embed failed after %d retries: %s", self.max_retries + 1, exc)

        raise RuntimeError(
            f"Cohere embedding failed after {self.max_retries + 1} attempts"
        ) from last_exc


def get_embedding_provider(
    tenant_id: int | None = None,
    voyage_api_key: str | None = None,
) -> EmbeddingProvider:
    """Config + tenant override'a gore embedding saglayici dondurur.

    Oncelik: tenant.embedding_provider > HUMETRIC_EMBEDDING_PROVIDER env > varsayilan (voyage).
    voyage_api_key verilirse Voyage icin platform key'i yerine bu kullanilir (BYO-key).
    """
    from . import config

    provider_name = os.environ.get("HUMETRIC_EMBEDDING_PROVIDER", "voyage")

    if provider_name == "openai":
        return OpenAIEmbeddingProvider(
            api_key=config.OPENAI_API_KEY,
            model="text-embedding-3-small",
            dimensions=config.EMBED_DIM_OPENAI,
            backoff_s=config.EMBED_BACKOFF_S,
            max_retries=config.EMBED_RETRIES,
        )
    if provider_name == "cohere":
        return CohereEmbeddingProvider(
            api_key=config.COHERE_API_KEY,
            model="embed-english-v3.0",
            dimensions=config.EMBED_DIM_COHERE,
            backoff_s=config.EMBED_BACKOFF_S,
            max_retries=config.EMBED_RETRIES,
        )

    vyg_key = voyage_api_key or config.VOYAGE_API_KEY
    return VoyageEmbeddingProvider(
        api_key=vyg_key,
        model=config.EMBED_MODEL,
        dimensions=config.EMBED_DIM_VOYAGE,
        backoff_s=config.EMBED_BACKOFF_S,
        max_retries=config.EMBED_RETRIES,
    )


async def get_tenant_embedding_provider(tenant_id: int, db) -> EmbeddingProvider:
    """Tenant override + BYO-key kontrolu yaparak embedding saglayici dondurur."""
    from . import config
    from .store import Store

    tenant = await Store.get_tenant_by_id(db, tenant_id)
    provider_name = (tenant.embedding_provider if tenant and tenant.embedding_provider
                     else config.EMBEDDING_PROVIDER)

    voyage_key: str | None = None
    try:
        voyage_key = await Store.decrypt_tenant_key(db, tenant_id, "voyage")
    except Exception:
        voyage_key = None

    if provider_name == "openai":
        return OpenAIEmbeddingProvider(
            api_key=config.OPENAI_API_KEY,
            dimensions=config.EMBED_DIM_OPENAI,
            backoff_s=config.EMBED_BACKOFF_S,
            max_retries=config.EMBED_RETRIES,
        )
    if provider_name == "cohere":
        return CohereEmbeddingProvider(
            api_key=config.COHERE_API_KEY,
            dimensions=config.EMBED_DIM_COHERE,
            backoff_s=config.EMBED_BACKOFF_S,
            max_retries=config.EMBED_RETRIES,
        )

    vyg_key = voyage_key or config.VOYAGE_API_KEY
    return VoyageEmbeddingProvider(
        api_key=vyg_key,
        model=config.EMBED_MODEL,
        dimensions=config.EMBED_DIM_VOYAGE,
        backoff_s=config.EMBED_BACKOFF_S,
        max_retries=config.EMBED_RETRIES,
    )
