"""Jury: reconcile several providers' answers into one decision.

When a tenant activates more than one LLM provider, the same request runs on
all of them in parallel and the outputs land here. Three strategies:

  best_of      Pick the member that agrees most with the rest of the panel
               (medoid). Nothing is invented and the result stays internally
               consistent, because it is exactly what one model produced.
               DEFAULT.
  field_merge  Merge field by field: median for numbers, mode for categoricals,
               majority-presence for list items. Reduces noise on numeric
               metrics, but yields a hybrid no single model wrote.
  majority     Vote on byte-identical outputs; without a majority, fall back to
               the first provider in the tenant's list.

Every run produces a JuryReport — agreement ratio, per-field disagreements and
per-member cost — so a decision stays auditable after the fact.

This module is deliberately dependency-free beyond pydantic: it reasons about
dumped dicts, never about provider SDKs.
"""

from __future__ import annotations

import json
import logging
import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Sequence

from pydantic import BaseModel

_log = logging.getLogger(__name__)

BEST_OF = "best_of"
FIELD_MERGE = "field_merge"
MAJORITY = "majority"
STRATEGIES = (BEST_OF, FIELD_MERGE, MAJORITY)

# Numeric fields within this relative distance count as agreement.
_NUMERIC_TOLERANCE = 0.05
# Strings longer than this are treated as free-form narrative and excluded from
# the agreement score. Two models never phrase a rationale identically, and
# counting that as disagreement would make the metric meaningless.
_NARRATIVE_LEN = 80


class JuryNoConsensus(RuntimeError):
    """Panel agreed less than the configured minimum.

    Raised only when a minimum is configured, so a low-confidence answer is
    never returned silently.
    """

    def __init__(self, message: str, *, report: "JuryReport | None" = None):
        super().__init__(message)
        self.report = report


@dataclass
class JuryVote:
    """One member's contribution — successful or not."""

    provider: str
    model: str
    ok: bool
    latency_ms: int = 0
    total_tokens: int = 0
    value: BaseModel | None = None
    error: str | None = None
    # Per-member call_meta (prompt/schema hashes, model). The winning member's
    # copy is promoted into the caller's call_meta so the trace records the
    # model that actually decided.
    meta: dict = field(default_factory=dict)


@dataclass
class JuryReport:
    """Auditable record of how the decision was reached."""

    strategy: str
    members: list[JuryVote]
    agreement: float
    disagreements: dict[str, list[Any]] = field(default_factory=dict)
    decisive_provider: str | None = None

    @property
    def successful(self) -> list[JuryVote]:
        return [m for m in self.members if m.ok]

    def summary(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "member_count": len(self.members),
            "ok_count": len(self.successful),
            "agreement": round(self.agreement, 4),
            "disagreement_fields": sorted(self.disagreements.keys()),
            "decisive_provider": self.decisive_provider,
            "members": [
                {
                    "provider": m.provider,
                    "model": m.model,
                    "ok": m.ok,
                    "latency_ms": m.latency_ms,
                    "total_tokens": m.total_tokens,
                    "error": m.error,
                }
                for m in self.members
            ],
        }


def deliberate(
    votes: Sequence[JuryVote],
    schema: type[BaseModel],
    *,
    strategy: str = BEST_OF,
    min_agreement: float = 0.0,
) -> tuple[BaseModel, JuryReport]:
    """Produce one decision plus its audit report.

    At least one successful vote is required; the caller guarantees that.
    """
    successful = [v for v in votes if v.ok and v.value is not None]
    if not successful:
        raise ValueError("deliberate() requires at least one successful vote")

    if strategy not in STRATEGIES:
        _log.warning("Unknown jury strategy %r, falling back to %s.", strategy, BEST_OF)
        strategy = BEST_OF

    dumps = [v.value.model_dump(mode="json") for v in successful]  # type: ignore[union-attr]
    agreement = _panel_agreement(dumps)
    disagreements = _disagreements(dumps)

    if len(successful) == 1:
        decided, winner = dumps[0], successful[0].provider
    elif strategy == BEST_OF:
        decided, winner = _best_of(dumps, successful)
    elif strategy == MAJORITY:
        decided, winner = _majority(dumps, successful)
    else:
        decided, winner = _merge_values(dumps), None

    report = JuryReport(
        strategy=strategy,
        members=list(votes),
        agreement=agreement,
        disagreements=disagreements,
        decisive_provider=winner,
    )

    if min_agreement > 0.0 and agreement < min_agreement and len(successful) > 1:
        raise JuryNoConsensus(
            f"Jury agreement {agreement:.2f} below threshold {min_agreement:.2f} "
            f"(diverging fields: {', '.join(sorted(disagreements)) or 'none'})",
            report=report,
        )

    try:
        decision = schema.model_validate(decided)
    except Exception as exc:
        # field_merge builds a hybrid object which can, rarely, fall outside the
        # schema. Fall back to the best single member rather than inventing one.
        _log.warning("Jury decision failed validation (%s), using best_of: %s", strategy, exc)
        fallback, winner = _best_of(dumps, successful)
        report.decisive_provider = winner
        report.strategy = f"{strategy}->{BEST_OF}"
        decision = schema.model_validate(fallback)

    return decision, report


# ── strategies ─────────────────────────────────────────────────


def _best_of(dumps: list[dict], votes: list[JuryVote]) -> tuple[dict, str]:
    """Pick the member closest to all the others (medoid)."""
    n = len(dumps)
    scores: list[float] = []
    for i in range(n):
        others = [_similarity(dumps[i], dumps[j]) for j in range(n) if j != i]
        scores.append(sum(others) / len(others) if others else 1.0)
    best = max(range(n), key=lambda i: (scores[i], -i))  # ties → list order
    return dumps[best], votes[best].provider


def _majority(dumps: list[dict], votes: list[JuryVote]) -> tuple[dict, str | None]:
    """Vote on identical outputs; without a majority use the first member."""
    buckets: dict[str, list[int]] = {}
    for i, d in enumerate(dumps):
        key = json.dumps(d, sort_keys=True, ensure_ascii=False, default=str)
        buckets.setdefault(key, []).append(i)
    largest = max(buckets.values(), key=len)
    if len(largest) > 1:
        idx = largest[0]
        return dumps[idx], votes[idx].provider
    return dumps[0], votes[0].provider


def _merge_values(values: list[Any]) -> Any:
    """Recursive field-wise merge (numbers → median, categoricals → mode)."""
    first = values[0]

    if isinstance(first, dict):
        dicts = [d for d in values if isinstance(d, dict)]
        keys: list[str] = []
        for d in dicts:
            for k in d:
                if k not in keys:
                    keys.append(k)
        return {k: _merge_values([d[k] for d in dicts if k in d]) for k in keys}

    if isinstance(first, list):
        return _merge_lists([d for d in values if isinstance(d, list)])

    if isinstance(first, bool) or first is None or isinstance(first, str):
        return _mode(values)

    if isinstance(first, (int, float)):
        numbers = [
            float(d) for d in values
            if isinstance(d, (int, float)) and not isinstance(d, bool)
        ]
        if not numbers:
            return first
        median = statistics.median(numbers)
        return int(round(median)) if all(isinstance(d, int) for d in values) else median

    return first


def _merge_lists(lists: list[list]) -> list:
    """Align items by identity key when there is one; otherwise pick a member."""
    if not lists:
        return []
    if len(lists) == 1:
        return lists[0]

    id_key = _identity_key(lists)
    if id_key is None:
        # Unalignable: take the median-length list verbatim rather than
        # fabricating a merge across positions that may not correspond.
        order = sorted(range(len(lists)), key=lambda i: len(lists[i]))
        return lists[order[len(order) // 2]]

    threshold = math.ceil(len(lists) / 2)
    groups: dict[Any, list[dict]] = {}
    positions: dict[Any, list[int]] = {}
    for items in lists:
        for pos, item in enumerate(items):
            if not isinstance(item, dict) or id_key not in item:
                continue
            k = item[id_key]
            groups.setdefault(k, []).append(item)
            positions.setdefault(k, []).append(pos)

    # Keep items seen by a majority of members, ordered by mean position.
    kept = [(k, v) for k, v in groups.items() if len(v) >= threshold]
    kept.sort(key=lambda kv: statistics.mean(positions[kv[0]]))
    return [_merge_values(items) for _, items in kept]


_IDENTITY_CANDIDATES = ("metric_key", "entity_id", "id", "key", "name", "label")


def _identity_key(lists: list[list]) -> str | None:
    """Find a field every item carries, usable to align lists across members."""
    items = [i for lst in lists for i in lst]
    if not items or not all(isinstance(i, dict) for i in items):
        return None
    for candidate in _IDENTITY_CANDIDATES:
        if all(candidate in i for i in items):
            return candidate
    return None


def _mode(values: list[Any]) -> Any:
    """Most frequent value; ties resolved by list order."""
    counts: dict[str, int] = {}
    first_seen: dict[str, Any] = {}
    for v in values:
        k = json.dumps(v, sort_keys=True, ensure_ascii=False, default=str)
        counts[k] = counts.get(k, 0) + 1
        first_seen.setdefault(k, v)
    best = max(counts, key=lambda k: (counts[k], -list(counts).index(k)))
    return first_seen[best]


# ── agreement measurement ──────────────────────────────────────


def _panel_agreement(dumps: list[dict]) -> float:
    """Mean pairwise similarity across all members (0..1)."""
    n = len(dumps)
    if n < 2:
        return 1.0
    scores = [
        _similarity(dumps[i], dumps[j])
        for i in range(n)
        for j in range(i + 1, n)
    ]
    return sum(scores) / len(scores) if scores else 1.0


def _similarity(a: Any, b: Any) -> float:
    matched, total = _compare(a, b)
    return matched / total if total else 1.0


def _compare(a: Any, b: Any) -> tuple[float, int]:
    """Return (matched leaf weight, total leaves)."""
    if isinstance(a, dict) and isinstance(b, dict):
        matched = 0.0
        total = 0
        for k in set(a) | set(b):
            if k in a and k in b:
                m, t = _compare(a[k], b[k])
                matched += m
                total += t
            else:
                total += 1  # present on one side only → divergence
        return matched, total

    if isinstance(a, list) and isinstance(b, list):
        if not a and not b:
            return 1.0, 1
        matched = 0.0
        total = 0
        for i in range(max(len(a), len(b))):
            if i < len(a) and i < len(b):
                m, t = _compare(a[i], b[i])
                matched += m
                total += t
            else:
                total += 1
        return matched, total or 1

    if isinstance(a, str) and isinstance(b, str):
        if len(a) > _NARRATIVE_LEN or len(b) > _NARRATIVE_LEN:
            return 0.0, 0  # narrative text is excluded from the score
        return (1.0 if a.strip().lower() == b.strip().lower() else 0.0), 1

    if isinstance(a, bool) or isinstance(b, bool):
        return (1.0 if a == b else 0.0), 1

    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        scale = max(1.0, abs(a), abs(b))
        return (1.0 if abs(a - b) <= _NUMERIC_TOLERANCE * scale else 0.0), 1

    return (1.0 if a == b else 0.0), 1


def _disagreements(dumps: list[dict]) -> dict[str, list[Any]]:
    """Collect diverging fields as `field.path → [value per member]`."""
    if len(dumps) < 2:
        return {}
    out: dict[str, list[Any]] = {}
    _walk(dumps, "", out, 0)
    return out


def _walk(values: list[Any], path: str, out: dict, depth: int) -> None:
    if depth > 6:
        return
    first = values[0]

    if isinstance(first, dict) and all(isinstance(v, dict) for v in values):
        for k in sorted({k for v in values for k in v}):
            _walk([v.get(k) for v in values], f"{path}.{k}" if path else k, out, depth + 1)
        return

    if isinstance(first, list) and all(isinstance(v, list) for v in values):
        if len({len(v) for v in values}) > 1:
            out[f"{path}[].length" if path else "[].length"] = [len(v) for v in values]
        for i in range(min(len(v) for v in values)):
            _walk([v[i] for v in values], f"{path}[{i}]", out, depth + 1)
        return

    matched, total = 0.0, 0
    for i in range(1, len(values)):
        m, t = _compare(values[0], values[i])
        matched += m
        total += t
    if total and matched < total:
        out[path or "(root)"] = list(values)
