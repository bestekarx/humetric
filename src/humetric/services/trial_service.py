"""Trial service — self-service 3-month Pro trial (start + expiry).

A tenant can start a free Pro trial exactly once: the tier becomes `pro` and
`trial_ends_at` is set to TRIAL_PERIOD_MONTHS months out. Once it expires,
the tenant reverts to the `free` tier — this is guaranteed both by the daily
worker sweep (`expire_due_trials`) and by the lazy check on every dashboard
read (`apply_trial_expiry`), so a trial can never be extended even if the
worker isn't running.

Tenants who convert to a paid Stripe subscription are marked
`trial_status='converted'` and the sweep leaves them untouched.
"""

from __future__ import annotations

import calendar
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import TRIAL_PERIOD_MONTHS
from ..db.models import Tenant

logger = logging.getLogger("humetric.trial")

TRIAL_TIER = "pro"


class TrialError(Exception):
    """Trial could not be started — `code` is carried into the API error envelope."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def add_months(start: datetime, months: int) -> datetime:
    """Add calendar months (Jan 31 + 1 month → Feb 28/29)."""
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def trial_info(tenant: Tenant) -> dict:
    """Trial summary embedded into dashboard/response payloads."""
    return {
        "trial_status": tenant.trial_status or "none",
        "trial_started_at": tenant.trial_started_at.isoformat() if tenant.trial_started_at else None,
        "trial_ends_at": tenant.trial_ends_at.isoformat() if tenant.trial_ends_at else None,
        "trial_available": (tenant.trial_status or "none") == "none" and tenant.tier == "free",
        "trial_days_left": days_left(tenant),
    }


def days_left(tenant: Tenant) -> int | None:
    if tenant.trial_status != "active" or not tenant.trial_ends_at:
        return None
    delta = tenant.trial_ends_at - datetime.now(timezone.utc)
    return max(0, delta.days)


async def start_trial(db: AsyncSession, tenant: Tenant) -> Tenant:
    """Start the 3-month Pro trial. Only once per tenant."""
    status = tenant.trial_status or "none"
    if status != "none":
        raise TrialError(
            "trial_already_used",
            "This account has already used its free Pro trial.",
        )
    if tenant.tier != "free":
        raise TrialError(
            "already_on_paid_tier",
            f"Account is already on the {tenant.tier} tier.",
        )

    now = datetime.now(timezone.utc)
    ends_at = add_months(now, TRIAL_PERIOD_MONTHS)

    tenant.tier = TRIAL_TIER
    tenant.subscription_status = "trialing"
    tenant.trial_started_at = now
    tenant.trial_ends_at = ends_at
    tenant.trial_status = "active"
    tenant.subscription_end = ends_at
    tenant.updated_at = now
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)

    logger.info("Trial started for tenant %d, ends %s", tenant.id, ends_at.isoformat())
    return tenant


def _is_due(tenant: Tenant, now: datetime) -> bool:
    return (
        tenant.trial_status == "active"
        and tenant.trial_ends_at is not None
        and tenant.trial_ends_at <= now
    )


def _downgrade(tenant: Tenant, now: datetime) -> None:
    tenant.tier = "free"
    tenant.subscription_status = "inactive"
    tenant.trial_status = "expired"
    tenant.subscription_end = tenant.trial_ends_at
    tenant.updated_at = now


async def apply_trial_expiry(db: AsyncSession, tenant: Tenant) -> bool:
    """Downgrade an expired trial at read time. True → downgraded."""
    now = datetime.now(timezone.utc)
    if not _is_due(tenant, now):
        return False
    _downgrade(tenant, now)
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)
    logger.info("Trial expired for tenant %d — downgraded to free", tenant.id)
    return True


async def expire_due_trials(db: AsyncSession) -> int:
    """Downgrade all expired trials (worker sweep, admin session)."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(Tenant).where(
            Tenant.trial_status == "active",
            Tenant.trial_ends_at.is_not(None),
            Tenant.trial_ends_at <= now,
        )
    )
    tenants = list(result.scalars().all())
    for tenant in tenants:
        _downgrade(tenant, now)
        db.add(tenant)
    if tenants:
        await db.commit()
        logger.info("Expired %d Pro trial(s)", len(tenants))
    return len(tenants)


def effective_tier(tier: str, trial_status: str | None, trial_ends_at: datetime | None) -> str:
    """Treat a trial as `free` once expired, even if not yet downgraded in the DB.

    Used on non-write paths like quota checks — so the trial never effectively
    extends even if the sweep is delayed.
    """
    if trial_status == "active" and trial_ends_at is not None:
        if trial_ends_at <= datetime.now(timezone.utc):
            return "free"
    return tier
