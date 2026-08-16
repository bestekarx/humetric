"""Read-time temporal decay: reduces stored confidence based on the metric's age.

Applying a multiplier at write time is conceptually wrong — decay should be
a function of the metric's *age*, not a one-time multiplier. This module
computes effective_confidence at read time, so the raw confidence stays
auditable.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from . import config


def decayed_confidence(
    stored_confidence: float,
    last_updated: datetime | None,
    now: datetime | None = None,
) -> float:
    """Apply age-based exponential decay to a stored confidence.

    Args:
        stored_confidence: Raw confidence from the DB (0-1).
        last_updated: When the metric was last updated. If None, no decay is applied.
        now: The comparison instant. If None, uses UTC now.

    Returns:
        Effective confidence (0-1). Returns the raw value if DECAY_ENABLED is off.
    """
    if not config.DECAY_ENABLED:
        return stored_confidence

    if last_updated is None:
        return stored_confidence

    if now is None:
        now = datetime.now(timezone.utc)

    age_days = (now - last_updated).total_seconds() / 86400.0
    if age_days < 0:
        age_days = 0

    return stored_confidence * math.exp(-config.DECAY_LAMBDA * age_days)
