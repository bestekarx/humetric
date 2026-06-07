"""Cikarilan metrikleri dogrular ve nihai degerleri belirler (Sonnet model)."""

from __future__ import annotations

from .. import config
from ..schema import CurationResult, ExtractedMetric, FinalMetric
from ..db.models import EntityMetric
from . import _load_prompt
from .base import structured_call

_DEFAULT_SYSTEM = _load_prompt("curator-default")
if not _DEFAULT_SYSTEM:
    _DEFAULT_SYSTEM = "Sen bir metrik kuratorusun. Cikarilan metrikleri mevcut profille karsilastirip dogrula."


async def curate_metrics(
    extracted: list[ExtractedMetric],
    existing_metrics: list[EntityMetric],
    entity_context: str = "",
    pack_def: dict | None = None,
    tenant_id: int | None = None,
) -> list[FinalMetric]:
    if not extracted:
        return []

    pack_prompt = None
    if pack_def and pack_def.get("prompts", {}).get("curation"):
        pack_prompt = pack_def["prompts"]["curation"]
    system = pack_prompt or _DEFAULT_SYSTEM

    existing_str = "\n".join(
        f"  - {m.metric_key}: {m.value:.2f} (guven: {m.confidence:.2f})"
        for m in existing_metrics
    ) if existing_metrics else "  (mevcut metrik yok)"

    extracted_str = "\n".join(
        f"  - {e.metric_key}: {e.value:.2f} (guven: {e.confidence:.2f}, gerekce: {e.reasoning})"
        for e in extracted
    )

    user = f"""Entity: {entity_context if entity_context else "Bilinmiyor"}

Mevcut metrikler:
{existing_str}

Cikarilan metrikler:
{extracted_str}

Her cikarilan metrik icin karar ver."""

    result = await structured_call(
        model=config.CURATOR_MODEL,
        system=system,
        user=user,
        schema=CurationResult,
        tool_ad="curate_metrics",
        tool_aciklama="Cikarilan metrikleri dogrula ve nihai degerleri belirle",
        tenant_id=tenant_id,
    )

    metric_type_map: dict[str, str] = {}
    if pack_def:
        for m in pack_def.get("metrics", []):
            metric_type_map[m.get("key", "")] = m.get("type", "float")

    existing_keys = {m.metric_key for m in existing_metrics}
    extracted_map = {e.metric_key: e for e in extracted}
    # Kurator "action" alanini serbest metin olarak doldurur (skip/reject/red/
    # insert/ekle/...). Metrigi dusuren aksiyonlari normalize et.
    DROP_ACTIONS = {"skip", "reject", "red", "atla", "drop", "ignore", "discard"}

    final_metrics: list[FinalMetric] = []
    for dec in result.decisions:
        src_value = dec.value
        src_confidence = dec.confidence

        if dec.action.strip().lower() in DROP_ACTIONS:
            # LLM ilk kez gozlemlenen, yuksek guvenli bir metrigi
            # nondeterministik olarak dusurebiliyor (skip/reject). Mevcut
            # profilde olmayan ve esik ustu cikarilan metrikleri ilk gozlem
            # olarak deterministik sekilde ekle — kurator yargisi mevcut
            # metriklerin guncellenmesinde korunur.
            is_new_metric = dec.metric_key not in existing_keys
            ext = extracted_map.get(dec.metric_key)
            first_obs_conf = ext.confidence if ext else 0.0
            if not (is_new_metric and first_obs_conf >= config.GUVEN_ESIGI):
                continue
            # override: kurator deger/guven doldurmamis (0) olabilir,
            # ilk gozlem icin cikarim degerlerini temel al.
            src_value = ext.value
            src_confidence = ext.confidence

        if src_confidence < config.GUVEN_ESIGI:
            continue

        # DB ck_entity_metric_value: value BETWEEN -1 AND 1. LLM bazen
        # aralik disi deger dondurebiliyor; insert'in (ve tum task
        # transaction'inin) patlamamasi icin kelepcele.
        final_value = max(-1.0, min(1.0, src_value))
        final_confidence = max(0.0, min(1.0, src_confidence))

        expected_type = metric_type_map.get(dec.metric_key, "float")
        if not _type_matches(final_value, expected_type):
            final_confidence = max(0.0, final_confidence - 0.2)

        final_metrics.append(FinalMetric(
            metric_key=dec.metric_key,
            value=final_value,
            confidence=final_confidence,
            reasoning=dec.reasoning,
        ))

    return final_metrics


def _type_matches(value: float, expected_type: str) -> bool:
    """Value tipi expected_type ile uyumlu mu?"""
    if expected_type == "float":
        return isinstance(value, (int, float))
    if expected_type == "int":
        return isinstance(value, int) or (isinstance(value, float) and value == int(value))
    if expected_type == "bool":
        return value in (0.0, 1.0, -1.0, 0, 1, -1) or isinstance(value, bool)
    if expected_type == "categorical":
        return True
    return True
