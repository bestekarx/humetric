"""Validates extracted metrics and determines their final values (Sonnet model)."""

from __future__ import annotations

from .. import config
from ..schema import CurationResult, ExtractedMetric, FinalMetric
from ..db.models import EntityMetric
from . import _load_prompt
from .base import structured_call

_DEFAULT_SYSTEM = _load_prompt("curator-default")
if not _DEFAULT_SYSTEM:
    _DEFAULT_SYSTEM = "You are a metric curation agent. Compare the extracted metrics against the existing profile and validate them."


async def curate_metrics(
    extracted: list[ExtractedMetric],
    existing_metrics: list[EntityMetric],
    entity_context: str = "",
    pack_def: dict | None = None,
    tenant_id: int | None = None,
    api_key: str | None = None,
    call_meta: dict | None = None,
) -> list[FinalMetric]:
    if not extracted:
        return []

    pack_prompt = None
    if pack_def and pack_def.get("prompts", {}).get("curation"):
        pack_prompt = pack_def["prompts"]["curation"]
    system = pack_prompt or _DEFAULT_SYSTEM

    existing_str = "\n".join(
        f"  - {m.metric_key}: {m.value:.2f} (confidence: {m.confidence:.2f})"
        for m in existing_metrics
    ) if existing_metrics else "  (no existing metrics)"

    extracted_str = "\n".join(
        f"  - {e.metric_key}: {e.value:.2f} (confidence: {e.confidence:.2f}, reasoning: {e.reasoning})"
        for e in extracted
    )

    user = f"""Entity: {entity_context if entity_context else "Unknown"}

Existing metrics:
{existing_str}

Extracted metrics:
{extracted_str}

Decide on each extracted metric."""

    result = await structured_call(
        model=config.CURATOR_MODEL,
        system=system,
        user=user,
        schema=CurationResult,
        tool_name="curate_metrics",
        tool_description="Validate the extracted metrics and determine final values",
        tenant_id=tenant_id,
        api_key=api_key,
        call_meta=call_meta,
    )

    metric_type_map: dict[str, str] = {}
    if pack_def:
        for m in pack_def.get("metrics", []):
            metric_type_map[m.get("key", "")] = m.get("type", "float")

    existing_keys = {m.metric_key for m in existing_metrics}
    extracted_map = {e.metric_key: e for e in extracted}
    # The curator fills the "action" field as free text (skip/reject/red/
    # insert/ekle/...). Normalize the actions that drop a metric.
    DROP_ACTIONS = {"skip", "reject", "red", "atla", "drop", "ignore", "discard"}

    final_metrics: list[FinalMetric] = []
    for dec in result.decisions:
        src_value = dec.value
        src_confidence = dec.confidence
        needs_review = False

        ext = extracted_map.get(dec.metric_key)
        if ext and ext.needs_review:
            needs_review = True

        if dec.action.strip().lower() in DROP_ACTIONS:
            is_new_metric = dec.metric_key not in existing_keys
            ext = extracted_map.get(dec.metric_key)
            first_obs_conf = ext.confidence if ext else 0.0
            if not (is_new_metric and first_obs_conf >= config.CONFIDENCE_THRESHOLD):
                continue
            src_value = ext.value
            src_confidence = ext.confidence

        if not needs_review and src_confidence < config.CONFIDENCE_THRESHOLD:
            continue

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
            needs_review=needs_review,
        ))

    return final_metrics


def _type_matches(value: float, expected_type: str) -> bool:
    """Does the value's type match expected_type?"""
    if expected_type == "float":
        return isinstance(value, (int, float))
    if expected_type == "int":
        return isinstance(value, int) or (isinstance(value, float) and value == int(value))
    if expected_type == "bool":
        return value in (0.0, 1.0, -1.0, 0, 1, -1) or isinstance(value, bool)
    if expected_type == "categorical":
        return True
    return True
