"""Hybrid RAG — pgvector cosine + full-text search + JSONB filter.

Note: bu modul Store.hybrid_search_entities() tarafindan kullanilir.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Entity


async def hybrid_search(
    db: AsyncSession,
    tenant_id: int,
    query_text: str | None = None,
    entity_type: str | None = None,
    filters: dict | None = None,
    top_k: int = 10,
    query_embedding: list[float] | None = None,
) -> list[Entity]:
    """Search entities with hybrid RAG. Deprecated: use Store.hybrid_search_entities()."""
    from ..store import Store
    return await Store.hybrid_search_entities(
        db, tenant_id=tenant_id, query_embedding=query_embedding,
        query_text=query_text, entity_type=entity_type,
        filters=filters, top_k=top_k,
    )
