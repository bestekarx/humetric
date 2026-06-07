"""Read-time temporal decay: stored confidence'i metriğin yaşına göre azaltır.

Write-time'da çarpan uygulamak kavramsal olarak yanlış — decay
metriğin *yaşının* fonksiyonu olmalı, tek seferlik çarpan değil.
Bu modül read-time'da effective_confidence hesaplar, ham confidence
denetlenebilir kalır.
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
    """Stored confidence'a yaş bazlı exponential decay uygula.

    Args:
        stored_confidence: DB'deki ham confidence (0-1).
        last_updated: Metriğin son güncellenme zamanı. None ise decay uygulanmaz.
        now: Karşılaştırma anı. None ise UTC now.

    Returns:
        Effective confidence (0-1). DECAY_ENABLED kapalıysa ham değeri döndürür.
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
