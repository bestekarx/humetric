"""Determines final metric values by merging extracted metrics with history.

Deterministic — no LLM call. The merge rule (confidence-weighted average,
higher new confidence pulls closer to the new value) used to be delegated to
a Sonnet call per signal; it is a formula, not a judgment call, so it is
computed directly in ``finalize_merge`` instead.
"""

from __future__ import annotations

from .. import config
from ..schema import ExtractedMetric, FinalMetric
from ..db.models import EntityMetric


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
    type-mismatch penalty. Returns None if the metric is dropped."""
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


def finalize_merge(
    extracted: list[ExtractedMetric],
    existing_metrics: list[EntityMetric],
    pack_def: dict | None = None,
) -> list[FinalMetric]:
    """Merge this signal's extracted metrics with the entity's current state.

    New metric_keys (no prior value) pass through as first observations.
    Metric_keys with a prior value get a confidence-weighted average: the
    higher the new confidence relative to the existing one, the closer the
    result lands to the new value.
    """
    type_map = _build_type_map(pack_def)
    existing_by_key = {m.metric_key: m for m in existing_metrics}

    final_metrics: list[FinalMetric] = []
    for e in extracted:
        existing = existing_by_key.get(e.metric_key)
        if existing is None:
            value, confidence, reasoning = e.value, e.confidence, e.reasoning
        else:
            total_confidence = existing.confidence + e.confidence
            weight_new = e.confidence / total_confidence if total_confidence > 0 else 1.0
            value = existing.value * (1 - weight_new) + e.value * weight_new
            confidence = existing.confidence * (1 - weight_new) + e.confidence * weight_new
            reasoning = (
                f"{e.reasoning} (birlestirildi: onceki={existing.value:.2f}"
                f"/conf={existing.confidence:.2f})"
            )

        fm = _finalize_metric(e.metric_key, value, confidence, e.needs_review, reasoning, type_map)
        if fm is not None:
            final_metrics.append(fm)
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
