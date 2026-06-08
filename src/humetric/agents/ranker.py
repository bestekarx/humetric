"""Entity'leri query'ye gore puanlar ve siralar (Sonnet model)."""

from __future__ import annotations

from .. import config
from ..schema import RankedResult, RankingResult
from ..db.models import Entity
from . import _load_prompt
from .base import structured_call

_DEFAULT_SYSTEM = _load_prompt("ranker-default")
if not _DEFAULT_SYSTEM:
    _DEFAULT_SYSTEM = "Sen bir entity siralama ajanisin. Sorguya gore entity'leri ilgililik skoruna gore sirala."


async def rank_entities(
    entities: list[Entity],
    query: str,
    rank_by: str | None = None,
    include_reasoning: bool = False,
    top_k: int = 10,
    tenant_id: int | None = None,
    api_key: str | None = None,
) -> list[RankedResult]:
    if not entities:
        return []

    entity_list = []
    for idx, ent in enumerate(entities):
        fields_str = ", ".join(f"{k}: {v}" for k, v in ent.fields.items()) if ent.fields else ""
        entity_list.append(
            f"[{idx}] id={ent.id}, type={ent.entity_type}"
            + (f", fields={{{fields_str}}}" if fields_str else "")
            + (f", text={ent.free_text[:200]}" if ent.free_text else "")
        )

    entities_text = "\n".join(entity_list)
    rank_hint = f"\nSiralama kriteri: {rank_by}" if rank_by else ""

    user = f"""Sorgu: {query}{rank_hint}

Entity listesi:
{entities_text}

En ilgili {min(top_k, len(entities))} entity'yi sirala."""

    result = await structured_call(
        model=config.CURATOR_MODEL,
        system=_DEFAULT_SYSTEM,
        user=user,
        schema=RankingResult,
        tool_name="rank_entities",
        tool_description="Score and rank entities against the query",
        tenant_id=tenant_id,
        api_key=api_key,
    )

    ranked: list[RankedResult] = []
    id_to_type = {e.id: e.entity_type for e in entities}
    for r in result.results:
        ranked.append(RankedResult(
            entity_id=r.entity_id,
            entity_type=id_to_type.get(r.entity_id, ""),
            score=r.score,
            reasoning=r.reasoning if include_reasoning else None,
        ))

    ranked.sort(key=lambda x: x.score, reverse=True)
    return ranked[:top_k]
