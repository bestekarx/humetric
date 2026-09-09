"""Pre-call measurement of what an extraction request is about to send.

Single responsibility: estimate the token size of the text going into an
extraction call, **before** the call, without asking the provider.

The estimate is local and deterministic on purpose. The budget gate runs on
every signal, so a provider count endpoint would add a network round trip and a
failure surface to every signal processed, and two of the four supported
providers do not expose one at all. The trade is a known inaccuracy, measured
offline by ``scripts/calibrate_token_estimate.py`` against recorded real usage
and corrected through ``config.TOKEN_ESTIMATE_CHARS_PER_TOKEN``.

Nothing here truncates, rewrites, or otherwise touches the text — it is a
read-only measurement.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import config


@dataclass(frozen=True)
class ContextMeasurement:
    """Token sizes of one extraction call's two halves.

    ``system_tokens`` is the cacheable prefix; ``user_tokens`` is the volatile
    tail that changes with every signal. Keeping them apart is what makes the
    cache question answerable at all -- the prefix is the half a provider can
    reuse.
    """

    system_tokens: int
    user_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.system_tokens + self.user_tokens


def estimate_tokens(text: str) -> int:
    """Estimate the token count of ``text``.

    Deterministic: the same input always returns the same number. Empty text
    measures 0 rather than a floor value, so an empty signal does not trip the
    budget gate.
    """
    if not text:
        return 0
    divisor = config.TOKEN_ESTIMATE_CHARS_PER_TOKEN
    if divisor <= 0:
        # A misconfigured divisor must not raise on the processing path; fall
        # back to the documented default rather than dividing by zero.
        divisor = 1.9
    return max(1, int(len(text) / divisor))


def measure_extract_inputs(system: str, user: str) -> ContextMeasurement:
    """Measure the system and user halves of an extraction call."""
    return ContextMeasurement(
        system_tokens=estimate_tokens(system),
        user_tokens=estimate_tokens(user),
    )
