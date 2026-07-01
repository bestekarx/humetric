"""Validates extracted metrics and determines their final values (Sonnet model)."""

from __future__ import annotations

from .. import config
from ..schema import CurationResult, ExtractedMetric, FinalMetric
from ..db.models import EntityMetric
from . import _load_prompt
from .multi_llm import structured_call_multi

_DEFAULT_SYSTEM = _load_prompt("curator-default")
if not _DEFAULT_SYSTEM:
    _DEFAULT_SYSTEM = "You are a metric curation agent. Compare the extracted metrics against the existing profile and validate them."

# The curator fills the "action" field as free text (skip/reject/red/
# insert/ekle/...). These actions drop a metric.
DROP_ACTIONS = {"skip", "reject", "red", "atla", "drop", "ignore", "discard"}


def _build_type_map(pack_def: dict | None) -> dict[str, str]:
    """metric_key -> declared type, from the pack definition."""
    type_map: dict[str, str] = {}
    if pack_def:
        for m in pack_def.get("metrics", []):
            type_map[m.get("key", "")] = m.get("type", "float")
    return type_map


def _finalize_metric(
    metric_key: str,
    value: float,
    confidence: float,
    needs_review: bool,
    reasoning: str,
    type_map: dict[str, str],
) -> FinalMetric | None:
    """Shared finalization: confidence-threshold filter, clamp to [-1, 1], and
    type-mismatch penalty. Used by both the LLM curator and the cold-start
    fast-path so their semantics never diverge. Returns None if dropped."""
    if not needs_review and confidence < config.CONFIDENCE_THRESHOLD:
        return None

    final_value = max(-1.0, min(1.0, value))
    final_confidence = max(0.0, min(1.0, confidence))

    expected_type = type_map.get(metric_key, "float")
    if not _type_matches(final_value, expected_type):
        final_confidence = max(0.0, final_confidence - 0.2)

    return FinalMetric(
        metric_key=metric_key,
        value=final_value,
        confidence=final_confidence,
        reasoning=reasoning,
        needs_review=needs_review,
    )


def build_curate_inputs(
    extracted: list[ExtractedMetric],
    existing_metrics: list[EntityMetric],
    entity_context: str = "",
    pack_def: dict | None = None,
) -> tuple[str, str]:
    """Build the (system, user) prompts for a curation call.

    Shared by the synchronous curator and the batch worker so both issue an
    identical request.
    """
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
    return system, user


def finalize_curation(
    result: CurationResult,
    extracted: list[ExtractedMetric],
    existing_metrics: list[EntityMetric],
    pack_def: dict | None = None,
) -> list[FinalMetric]:
    """Turn the LLM curator's decisions into final metrics.

    Factored out so the batch worker can reuse it after parsing a batched
    curation response.
    """
    type_map = _build_type_map(pack_def)
    existing_keys = {m.metric_key for m in existing_metrics}
    extracted_map = {e.metric_key: e for e in extracted}

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
            first_obs_conf = ext.confidence if ext else 0.0
            if not (is_new_metric and first_obs_conf >= config.CONFIDENCE_THRESHOLD):
                continue
            src_value = ext.value
            src_confidence = ext.confidence

        fm = _finalize_metric(
            dec.metric_key, src_value, src_confidence, needs_review, dec.reasoning, type_map,
        )
        if fm is not None:
            final_metrics.append(fm)

    return final_metrics


def finalize_first_observation(
    extracted: list[ExtractedMetric],
    pack_def: dict | None = None,
) -> list[FinalMetric]:
    """Cold-start fast-path: finalize extracted metrics as first observations,
    skipping the LLM curator entirely.

    On an entity with no existing metrics there is nothing to reconcile, and
    the curator already inserts a brand-new metric whose first-observation
    confidence clears the threshold (see ``finalize_curation``). This produces
    the same result at zero extra LLM cost.
    """
    type_map = _build_type_map(pack_def)
    final_metrics: list[FinalMetric] = []
    for e in extracted:
        fm = _finalize_metric(
            e.metric_key, e.value, e.confidence, e.needs_review, e.reasoning, type_map,
        )
        if fm is not None:
            final_metrics.append(fm)
    return final_metrics


async def curate_metrics(
    extracted: list[ExtractedMetric],
    existing_metrics: list[EntityMetric],
    entity_context: str = "",
    pack_def: dict | None = None,
    tenant_id: int | None = None,
    api_key: str | None = None,
    provider: str | None = None,
    call_meta: dict | None = None,
) -> list[FinalMetric]:
    if not extracted:
        return []

    system, user = build_curate_inputs(extracted, existing_metrics, entity_context, pack_def)
    resolved_provider = provider or "anthropic"
    model = config.get_curator_model(resolved_provider)

    result = await structured_call_multi(
        provider=resolved_provider,
        model=model,
        api_key=api_key,
        system=system,
        user=user,
        schema=CurationResult,
        tool_name="curate_metrics",
        tool_description="Validate the extracted metrics and determine final values",
        tenant_id=tenant_id,
        call_meta=call_meta,
    )

    return finalize_curation(result, extracted, existing_metrics, pack_def)


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
