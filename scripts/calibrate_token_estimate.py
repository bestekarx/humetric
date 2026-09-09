#!/usr/bin/env python3
"""Report how far context.py's local token estimate drifts from reality.

The estimate in ``humetric.context`` is a characters-per-token division, chosen
so the budget gate costs nothing at request time. That trade only holds if
somebody occasionally checks how wrong it is -- this is that check.

**It measures against recorded data, not a provider count endpoint.** For every
metric written since the measurement landed, ``trace_data.measured_input_tokens``
holds what we estimated and ``llm_call_record.input_tokens`` holds what the
provider actually charged for the same call. Comparing those two needs no
network call, costs nothing, and works for all four providers -- including the
two that expose no count endpoint at all.

It changes no file. It prints a deviation and a suggested divisor; a human
decides whether to move ``HUMETRIC_TOKEN_ESTIMATE_CHARS_PER_TOKEN``.

    python scripts/calibrate_token_estimate.py
    python scripts/calibrate_token_estimate.py --provider deepseek --limit 500
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlalchemy import create_engine, text  # noqa: E402

from humetric import config  # noqa: E402

# Estimate and actual are joined per signal. A signal can produce several
# metrics (same estimate on each) and several calls (retries, re-asks); we take
# one estimate per signal and the first call's input_tokens, so a retried signal
# does not get counted twice with an inflated actual.
_QUERY = """
WITH est AS (
    SELECT DISTINCT ON (signal_id)
           signal_id,
           (trace_data ->> 'measured_input_tokens')::numeric AS estimated
    FROM entity_metric
    WHERE signal_id IS NOT NULL
      AND trace_data ->> 'measured_input_tokens' IS NOT NULL
    ORDER BY signal_id, last_updated DESC
),
act AS (
    SELECT DISTINCT ON (signal_id)
           signal_id, input_tokens AS actual, provider
    FROM llm_call_record
    WHERE signal_id IS NOT NULL AND input_tokens IS NOT NULL
    ORDER BY signal_id, created_at ASC
)
SELECT est.signal_id, est.estimated, act.actual, act.provider
FROM est JOIN act USING (signal_id)
WHERE (CAST(:provider AS text) IS NULL OR act.provider = CAST(:provider AS text))
LIMIT :limit
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--provider", help="restrict to one provider")
    ap.add_argument("--limit", type=int, default=1000)
    args = ap.parse_args()

    url = config.DATABASE_URL
    if not url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2

    engine = create_engine(url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(_QUERY), {"provider": args.provider, "limit": args.limit}
        ).fetchall()

    if not rows:
        print(
            "No paired rows yet.\n\n"
            "This tool needs both halves to exist: trace_data.measured_input_tokens\n"
            "(written by the worker) and llm_call_record.input_tokens (written by the\n"
            "provider branches). Process some signals first, then run it again."
        )
        return 0

    ratios = [float(r.estimated) / float(r.actual) for r in rows if r.actual]
    if not ratios:
        print("Paired rows found, but every actual was zero.")
        return 0

    mean_ratio = statistics.fmean(ratios)
    median_ratio = statistics.median(ratios)
    current = config.TOKEN_ESTIMATE_CHARS_PER_TOKEN
    # estimate = chars / divisor. If we estimate 1.2x too high, the divisor has
    # to grow by the same factor to bring it back down.
    suggested = current * median_ratio

    print(f"Paired signals      : {len(ratios)}")
    if args.provider:
        print(f"Provider            : {args.provider}")
    print(f"Current divisor     : {current:.2f} chars/token")
    print(f"Estimate / actual   : mean {mean_ratio:.3f}, median {median_ratio:.3f}")
    print("  (1.0 = perfect, >1 = over-estimating, <1 = under-estimating)")
    print(f"Deviation (median)  : {abs(median_ratio - 1) * 100:.1f}%")
    print(f"Suggested divisor   : {suggested:.2f}")
    print()
    print("This tool changes nothing. To adopt the suggestion, set")
    print(f"  HUMETRIC_TOKEN_ESTIMATE_CHARS_PER_TOKEN={suggested:.2f}")
    print("or update the default in src/humetric/config.py.")
    print()
    print(
        "Note: over-estimating is the safe direction. It trips the budget gate\n"
        "early, which flags a signal for review; under-estimating lets an\n"
        "over-budget context through unflagged."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
