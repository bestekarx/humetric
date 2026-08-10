"""Free Pro trial tests — pure unit, no live DB.

The bug these guard against: the dashboard reads `trial_available`, and a
missing/false value makes the UI tell the tenant it has "already used" its
trial. A brand-new tenant must therefore always report trial_available=True.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from humetric import config
from humetric.schema import TenantDashboardResponse, TrialResponse
from humetric.store import _display_tz, _empty_trial_state, _trial_days_left, _trial_state


def tenant(**kw) -> SimpleNamespace:
    base = dict(
        id=1,
        tier="free",
        trial_status="none",
        trial_started_at=None,
        trial_ends_at=None,
        subscription_status="inactive",
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ── a new tenant can start a trial ─────────────────────────────


def test_new_tenant_has_trial_available():
    state = _trial_state(tenant())
    assert state["trial_available"] is True
    assert state["trial_status"] == "none"
    assert state["trial_days_left"] is None


@pytest.mark.parametrize("status", ["active", "expired"])
def test_used_trial_is_not_available_again(status):
    state = _trial_state(tenant(trial_status=status))
    assert state["trial_available"] is False


def test_active_trial_reports_days_left():
    ends = datetime.now(timezone.utc) + timedelta(days=30)
    state = _trial_state(tenant(trial_status="active", trial_ends_at=ends, tier="pro"))
    assert state["trial_status"] == "active"
    assert 29 <= state["trial_days_left"] <= 30
    assert state["tier"] == "pro"


def test_expired_trial_has_no_days_left():
    ends = datetime.now(timezone.utc) - timedelta(days=1)
    state = _trial_state(tenant(trial_status="expired", trial_ends_at=ends))
    assert state["trial_days_left"] is None


# ── days-left arithmetic ───────────────────────────────────────


def test_days_left_is_never_negative():
    past = datetime.now(timezone.utc) - timedelta(days=10)
    assert _trial_days_left(past) == 0


def test_days_left_none_without_an_end_date():
    assert _trial_days_left(None) is None


def test_days_left_rounds_a_partial_day_up():
    """Half a day remaining should read as 1 day, not 0 — the tenant still
    has access today."""
    ends = datetime.now(timezone.utc) + timedelta(hours=12)
    assert _trial_days_left(ends) == 1


def test_days_left_counts_calendar_days_not_elapsed_hours():
    """Bir günlük denemenin ilk saniyesinde de son saatinde de aynı sayı
    okunmalı: sayaç geçen saate göre değil, takvim gününe göre düşer. Eski
    timestamp farkı burada 90 ile 91 arasında salınıyordu."""
    ends = datetime.now(timezone.utc) + timedelta(days=config.TRIAL_DAYS)
    assert _trial_days_left(ends) == config.TRIAL_DAYS


def test_days_left_flips_at_local_midnight():
    """Yerel gece yarısını geçen 1 saatlik kalan süre 1 gün olarak okunur;
    aynı gün içindeki 1 saat de 1 — hiçbir zaman 0 değil."""
    tz = _display_tz()
    now_local = datetime.now(tz)
    just_before_midnight = now_local.replace(hour=23, minute=59, second=0, microsecond=0)
    if just_before_midnight <= now_local:
        just_before_midnight += timedelta(days=1)
    assert _trial_days_left(just_before_midnight.astimezone(timezone.utc)) >= 1
    assert _trial_days_left((now_local + timedelta(hours=1)).astimezone(timezone.utc)) >= 1


# ── response contract the dashboard depends on ─────────────────


def test_dashboard_response_always_carries_trial_fields():
    """A missing field would deserialize as undefined in the UI and render the
    "already used" branch — the exact bug this feature fixes."""
    payload = TenantDashboardResponse(
        tenant_id=1, tier="free", subscription_status="inactive",
    ).model_dump()
    for field in ("trial_status", "trial_available", "trial_ends_at", "trial_days_left"):
        assert field in payload, f"{field} missing from dashboard response"


def test_dashboard_defaults_are_safe_for_a_new_tenant():
    payload = TenantDashboardResponse(tenant_id=1, tier="free", subscription_status="inactive")
    assert payload.trial_status == "none"


def test_trial_response_shape():
    r = TrialResponse(
        tenant_id=1, tier="pro", trial_status="active",
        trial_started_at=datetime.now(timezone.utc),
        trial_ends_at=datetime.now(timezone.utc) + timedelta(days=config.TRIAL_DAYS),
        trial_days_left=config.TRIAL_DAYS,
    )
    assert r.tier == "pro"
    assert r.trial_days_left == config.TRIAL_DAYS


def test_empty_state_never_offers_a_trial():
    """Used when the tenant row is missing; offering a trial there would let an
    unknown caller flip tiers."""
    assert _empty_trial_state()["trial_available"] is False


def test_trial_length_is_three_months_by_default():
    assert config.TRIAL_DAYS == 90
    assert config.TRIAL_TIER == "pro"
