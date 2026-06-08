"""Extracts metrics from signal text (Haiku model)."""

from __future__ import annotations

from .. import config
from ..schema import ExtractedMetric, ExtractionResult
from . import _load_prompt
from .base import structured_call

_DEFAULT_SYSTEM = _load_prompt("extractor-default")
if not _DEFAULT_SYSTEM:
    _DEFAULT_SYSTEM = "You are a metric extraction agent. Extract measurable metrics from signal text."


async def extract_metrics(
    signal_text: str,
    entity_context: str = "",
    pack_prompt: str | None = None,
    pack_metrics: list[dict] | None = None,
    tenant_id: int | None = None,
) -> list[ExtractedMetric]:
    system = pack_prompt or _DEFAULT_SYSTEM

    # If the pack defines metrics, force the model to use ONLY these
    # metric_keys — otherwise the model may freely invent keys (e.g.
    # 'financial_regularity') and bypass the pack's canonical keys (e.g.
    # 'financial_status') along with their KVKK/consent rules.
    allowed_block = ""
    if pack_metrics:
        lines = []
        for m in pack_metrics:
            key = m.get("key")
            if not key:
                continue
            description = m.get("prompt") or m.get("label") or ""
            mtype = m.get("type", "float")
            lines.append(f"  - {key} ({mtype}): {description}")
        if lines:
            allowed_block = (
                "\nUse ONLY the metric_keys defined below. Do NOT invent new "
                "keys that aren't in the list. Skip metrics that have no "
                "counterpart in the signal:\n" + "\n".join(lines) + "\n"
            )

    user = f"""Entity: {entity_context if entity_context else "Unknown"}
{allowed_block}
Signal text: {signal_text}

Extract metrics from the signal above."""
    result = await structured_call(
        model=config.AGENT_MODEL,
        system=system,
        user=user,
        schema=ExtractionResult,
        tool_name="extract_metrics",
        tool_description="Extract metrics from the signal text",
        tenant_id=tenant_id,
    )
    return result.metrics
