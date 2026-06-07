"""Sinyal metninden metric cikarimi yapar (Haiku model)."""

from __future__ import annotations

from .. import config
from ..schema import ExtractedMetric, ExtractionResult
from . import _load_prompt
from .base import structured_call

_DEFAULT_SYSTEM = _load_prompt("extractor-default")
if not _DEFAULT_SYSTEM:
    _DEFAULT_SYSTEM = "Sen bir metrik cikarim ajanisin. Sinyal metninden olculebilir metrikler cikar."


async def extract_metrics(
    signal_text: str,
    entity_context: str = "",
    pack_prompt: str | None = None,
    pack_metrics: list[dict] | None = None,
    tenant_id: int | None = None,
) -> list[ExtractedMetric]:
    system = pack_prompt or _DEFAULT_SYSTEM

    # Pack metrik tanimlari varsa, modeli SADECE bu metric_key'leri
    # kullanmaya zorla — aksi halde model serbest (or. 'finansal_duzenlilik')
    # anahtarlar uretip pack'in kanonik anahtarlarini (or. 'mali_durum')
    # ve onlara bagli KVKK/consent kurallarini atlar.
    allowed_block = ""
    if pack_metrics:
        satirlar = []
        for m in pack_metrics:
            key = m.get("key")
            if not key:
                continue
            aciklama = m.get("prompt") or m.get("label") or ""
            mtype = m.get("type", "float")
            satirlar.append(f"  - {key} ({mtype}): {aciklama}")
        if satirlar:
            allowed_block = (
                "\nYALNIZCA asagida tanimli metric_key'leri kullan. Listede "
                "olmayan yeni anahtar URETME. Sinyalde karsiligi olmayan "
                "metrikleri atla:\n" + "\n".join(satirlar) + "\n"
            )

    user = f"""Entity: {entity_context if entity_context else "Bilinmiyor"}
{allowed_block}
Sinyal metni: {signal_text}

Yukaridaki sinyalden metrikleri cikar."""
    result = await structured_call(
        model=config.AGENT_MODEL,
        system=system,
        user=user,
        schema=ExtractionResult,
        tool_ad="extract_metrics",
        tool_aciklama="Sinyal metninden metrik cikar",
        tenant_id=tenant_id,
    )
    return result.metrics
