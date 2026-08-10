"""Jury consensus tests — pure unit, no live DB or LLM.

Covers the three strategies, agreement measurement, and the multi-provider
selection validation that guards them.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from humetric import config
from humetric.agents.jury import (
    BEST_OF,
    FIELD_MERGE,
    MAJORITY,
    JuryNoConsensus,
    JuryVote,
    deliberate,
)
from humetric.schema import TenantKeysUpdate


class Decision(BaseModel):
    metric_key: str
    value: float
    confidence: float


class Result(BaseModel):
    decisions: list[Decision]
    summary: str | None = None


def vote(provider: str, decisions: list[tuple[str, float, float]], summary: str | None = None) -> JuryVote:
    return JuryVote(
        provider=provider,
        model=f"{provider}-model",
        ok=True,
        latency_ms=100,
        value=Result(
            decisions=[Decision(metric_key=k, value=v, confidence=c) for k, v, c in decisions],
            summary=summary,
        ),
    )


def failed(provider: str) -> JuryVote:
    return JuryVote(provider=provider, model="x", ok=False, latency_ms=5, error="429 rate limited")


# ── basics ─────────────────────────────────────────────────────


def test_single_successful_member_passes_through():
    a = vote("anthropic", [("punctuality", 0.8, 0.9)])
    decision, report = deliberate([a, failed("openai")], Result, strategy=BEST_OF)
    assert decision.decisions[0].value == 0.8
    assert report.agreement == 1.0
    assert len(report.successful) == 1


def test_no_successful_member_raises():
    with pytest.raises(ValueError):
        deliberate([failed("a"), failed("b")], Result)


def test_full_agreement_scores_one():
    decisions = [("punctuality", 0.8, 0.9)]
    _, report = deliberate(
        [vote("anthropic", decisions), vote("openai", decisions), vote("google", decisions)],
        Result,
    )
    assert report.agreement == 1.0
    assert report.disagreements == {}


# ── best_of ────────────────────────────────────────────────────


def test_best_of_rejects_the_outlier():
    a = vote("anthropic", [("punctuality", 0.80, 0.90)])
    b = vote("openai", [("punctuality", 0.79, 0.89)])
    outlier = vote("google", [("punctuality", -0.90, 0.10)])

    decision, report = deliberate([a, b, outlier], Result, strategy=BEST_OF)
    assert report.decisive_provider in ("anthropic", "openai")
    assert decision.decisions[0].value > 0.5


def test_best_of_returns_one_members_output_verbatim():
    """best_of never fabricates a hybrid."""
    a = vote("anthropic", [("x", 0.8, 0.9)], summary="A")
    b = vote("openai", [("x", 0.2, 0.3)], summary="B")
    decision, report = deliberate([a, b], Result, strategy=BEST_OF)
    source = a if report.decisive_provider == "anthropic" else b
    assert decision.model_dump() == source.value.model_dump()


# ── field_merge ────────────────────────────────────────────────


def test_field_merge_takes_the_median():
    votes = [
        vote("anthropic", [("x", 0.8, 0.9)]),
        vote("openai", [("x", 0.7, 0.8)]),
        vote("google", [("x", 0.2, 0.4)]),
    ]
    decision, _ = deliberate(votes, Result, strategy=FIELD_MERGE)
    assert decision.decisions[0].value == pytest.approx(0.7)
    assert decision.decisions[0].confidence == pytest.approx(0.8)


def test_field_merge_drops_a_metric_only_one_member_saw():
    votes = [
        vote("anthropic", [("shared", 0.5, 0.8)]),
        vote("openai", [("shared", 0.6, 0.8)]),
        vote("google", [("shared", 0.55, 0.8), ("google_only", 0.9, 0.9)]),
    ]
    decision, _ = deliberate(votes, Result, strategy=FIELD_MERGE)
    assert {d.metric_key for d in decision.decisions} == {"shared"}


def test_field_merge_keeps_a_metric_a_majority_saw():
    votes = [
        vote("anthropic", [("shared", 0.5, 0.8), ("second", 0.4, 0.7)]),
        vote("openai", [("shared", 0.6, 0.8), ("second", 0.5, 0.7)]),
        vote("google", [("shared", 0.55, 0.8)]),
    ]
    decision, _ = deliberate(votes, Result, strategy=FIELD_MERGE)
    assert {d.metric_key for d in decision.decisions} == {"shared", "second"}


# ── majority ───────────────────────────────────────────────────


def test_majority_picks_the_repeated_output():
    same = [("x", 0.5, 0.7)]
    other = [("x", -0.9, 0.2)]
    decision, report = deliberate(
        [vote("anthropic", other), vote("openai", same), vote("google", same)],
        Result,
        strategy=MAJORITY,
    )
    assert decision.decisions[0].value == 0.5
    assert report.decisive_provider in ("openai", "google")


def test_majority_without_a_majority_uses_the_first_member():
    decision, report = deliberate(
        [vote("anthropic", [("x", 0.1, 0.5)]), vote("openai", [("x", 0.9, 0.5)])],
        Result,
        strategy=MAJORITY,
    )
    assert decision.decisions[0].value == 0.1
    assert report.decisive_provider == "anthropic"


# ── agreement threshold & report ───────────────────────────────


def test_min_agreement_below_threshold_raises():
    a = vote("anthropic", [("x", 0.9, 0.9)])
    b = vote("openai", [("y", -0.9, 0.1)])
    with pytest.raises(JuryNoConsensus) as exc:
        deliberate([a, b], Result, strategy=BEST_OF, min_agreement=0.9)
    assert exc.value.report is not None


def test_min_agreement_ignored_for_a_single_member():
    a = vote("anthropic", [("x", 0.9, 0.9)])
    decision, _ = deliberate([a, failed("openai")], Result, min_agreement=0.99)
    assert decision.decisions[0].value == 0.9


def test_disagreement_paths_are_reported():
    a = vote("anthropic", [("x", 0.9, 0.9)])
    b = vote("openai", [("x", 0.1, 0.9)])
    _, report = deliberate([a, b], Result)
    assert "decisions[0].value" in report.disagreements
    assert "decisions[0].confidence" not in report.disagreements


def test_narrative_text_does_not_lower_agreement():
    """Two models never phrase a rationale identically; that is not divergence."""
    a = vote("anthropic", [("x", 0.5, 0.8)], summary="a" * 200)
    b = vote("openai", [("x", 0.5, 0.8)], summary="b" * 200)
    _, report = deliberate([a, b], Result)
    assert report.agreement == 1.0


def test_small_numeric_difference_counts_as_agreement():
    a = vote("anthropic", [("x", 0.800, 0.90)])
    b = vote("openai", [("x", 0.820, 0.90)])
    _, report = deliberate([a, b], Result)
    assert report.agreement == 1.0


def test_report_summary_carries_member_detail():
    a = vote("anthropic", [("x", 0.5, 0.8)])
    _, report = deliberate([a, failed("google")], Result)
    summary = report.summary()
    assert summary["member_count"] == 2
    assert summary["ok_count"] == 1
    assert summary["members"][1]["error"] is not None


# ── selection validation ───────────────────────────────────────


def test_multi_provider_selection_accepted():
    body = TenantKeysUpdate(llm_providers=["anthropic", "openai"])
    assert body.llm_providers == ["anthropic", "openai"]


def test_duplicate_providers_collapse():
    """A repeated provider would otherwise be billed twice per request."""
    body = TenantKeysUpdate(llm_providers=["openai", "anthropic", "openai"])
    assert body.llm_providers == ["openai", "anthropic"]


def test_unknown_provider_rejected():
    with pytest.raises(ValidationError):
        TenantKeysUpdate(llm_providers=["anthropic", "not-a-provider"])


def test_empty_selection_rejected():
    with pytest.raises(ValidationError):
        TenantKeysUpdate(llm_providers=[])


def test_too_many_providers_rejected():
    with pytest.raises(ValidationError):
        TenantKeysUpdate(llm_providers=["anthropic", "openai", "google", "deepseek"] * 3)


def test_unknown_jury_strategy_rejected():
    with pytest.raises(ValidationError):
        TenantKeysUpdate(llm_jury_strategy="coin_flip")


@pytest.mark.parametrize("strategy", [BEST_OF, FIELD_MERGE, MAJORITY])
def test_valid_jury_strategies_accepted(strategy):
    assert TenantKeysUpdate(llm_jury_strategy=strategy).llm_jury_strategy == strategy


def test_selection_respects_disabled_providers(monkeypatch):
    monkeypatch.setattr(config, "ENABLED_LLM_PROVIDERS", ["anthropic"])
    with pytest.raises(ValidationError):
        TenantKeysUpdate(llm_providers=["anthropic", "openai"])
