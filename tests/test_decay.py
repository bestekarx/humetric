"""Temporal decay birim testleri."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from humetric.decay import decayed_confidence
from humetric import config


def test_no_decay_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "DECAY_ENABLED", False)
    now = datetime.now(timezone.utc)
    last_updated = now - timedelta(days=500)
    result = decayed_confidence(0.8, last_updated, now=now)
    assert result == 0.8


def test_no_decay_when_last_updated_none():
    result = decayed_confidence(0.8, None)
    assert result == 0.8


def test_age_zero_equals_stored():
    now = datetime.now(timezone.utc)
    result = decayed_confidence(0.8, last_updated=now, now=now)
    assert result == pytest.approx(0.8, rel=1e-9)


def test_half_life_365_days():
    now = datetime.now(timezone.utc)
    last_updated = now - timedelta(days=365)
    result = decayed_confidence(0.8, last_updated, now=now)
    assert result == pytest.approx(0.4, abs=0.02)


def test_negative_age_handled():
    now = datetime.now(timezone.utc)
    last_updated = now + timedelta(days=10)
    result = decayed_confidence(0.8, last_updated, now=now)
    assert result == 0.8


def test_confidence_capped_at_zero():
    now = datetime.now(timezone.utc)
    last_updated = now - timedelta(days=5000)
    result = decayed_confidence(0.8, last_updated, now=now)
    assert result >= 0.0
