"""Stripe billing servisi — Checkout, Customer Portal, Webhook handler (Spec 026)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import stripe

from ..config import (
    HUMETRIC_BASE_URL,
    STRIPE_ANALYZER_PRICE_ID,
    STRIPE_ENTERPRISE_MONTHLY_PRICE_ID,
    STRIPE_PRO_MONTHLY_PRICE_ID,
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
)

logger = logging.getLogger("humetric.stripe")

stripe.api_key = STRIPE_SECRET_KEY

TIER_PRICE_MAP = {
    "pro": STRIPE_PRO_MONTHLY_PRICE_ID,
    "enterprise": STRIPE_ENTERPRISE_MONTHLY_PRICE_ID,
}


async def create_customer(email: str, tenant_id: int) -> stripe.Customer:
    customer = stripe.Customer.create(
        email=email,
        metadata={"tenant_id": str(tenant_id)},
    )
    return customer


async def create_checkout_session(
    customer_id: str, tier: str, tenant_id: int
) -> str:
    price_id = TIER_PRICE_MAP.get(tier)
    if not price_id:
        raise ValueError(f"Unknown tier: {tier}")
    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        mode="subscription",
        success_url=f"{HUMETRIC_BASE_URL}/dashboard?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{HUMETRIC_BASE_URL}/pricing",
        metadata={"tenant_id": str(tenant_id), "tier": tier},
    )
    return session.url


async def create_analyzer_checkout_session(
    customer_id: str, tenant_id: int, analysis_session_id: int,
) -> str:
    """One-time payment checkout for a single Metric Analyzer scan.

    ``mode="payment"`` (not "subscription") — this is a single charge per
    scan, independent of the tenant's subscription tier.
    """
    if not STRIPE_ANALYZER_PRICE_ID:
        raise ValueError("STRIPE_ANALYZER_PRICE_ID is not configured")
    return_base = f"{HUMETRIC_BASE_URL}/dashboard/analyzer.html?session={analysis_session_id}"
    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": STRIPE_ANALYZER_PRICE_ID, "quantity": 1}],
        mode="payment",
        success_url=return_base,
        cancel_url=f"{return_base}&cancelled=1",
        metadata={
            "kind": "analyzer_scan",
            "tenant_id": str(tenant_id),
            "analysis_session_id": str(analysis_session_id),
        },
    )
    return session.url


async def create_customer_portal_session(customer_id: str) -> str:
    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{HUMETRIC_BASE_URL}/dashboard",
    )
    return session.url


async def verify_webhook_signature(payload: bytes, sig_header: str) -> stripe.Event:
    return stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)


async def handle_webhook(event: stripe.Event, db_session) -> dict[str, Any]:
    """Process webhook event — update tenant status."""
    from ..db.models import Tenant
    from sqlalchemy import update

    event_type = event["type"]
    data = event["data"]["object"]
    metadata = data.get("metadata") or {}

    # Metric Analyzer one-time scan payment — handled entirely separately from
    # the subscription flow below. Must not fall through to the tier/status
    # update logic, which would otherwise misread this as a subscription
    # checkout and incorrectly set tier="pro".
    if event_type == "checkout.session.completed" and metadata.get("kind") == "analyzer_scan":
        from sqlalchemy import text as _text

        from ..store import Store

        tenant_id = int(metadata["tenant_id"])
        analysis_session_id = int(metadata["analysis_session_id"])

        # analysis_session is RLS-forced; this session runs with no tenant
        # context (get_async_session_factory), so the GUC must be set
        # explicitly before touching tenant-scoped rows (fail-closed RLS).
        await db_session.execute(
            _text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tenant_id)},
        )
        transitioned = await Store.transition_analysis_status(
            db_session, analysis_session_id, tenant_id,
            from_status="pending_payment", to_status="processing",
            updates={"paid_at": datetime.now(timezone.utc), "checkout_session_id": data.get("id")},
        )
        if transitioned:
            await Store.create_task(db_session, {
                "tenant_id": tenant_id,
                "signal_id": None,
                "task_type": "analysis_scan",
                "status": "queued",
                "payload": {"session_id": analysis_session_id, "mode": "initial"},
            })
        return {
            "handled": True, "event": event_type, "kind": "analyzer_scan",
            "transitioned": transitioned,
        }

    customer_id = data.get("customer")

    if not customer_id:
        logger.warning("Webhook missing customer_id: %s", event_type)
        return {"handled": False, "reason": "no_customer_id"}

    subscription_status: str | None = None
    tier: str | None = None

    if event_type == "checkout.session.completed":
        subscription_status = "active"
        tier = data.get("metadata", {}).get("tier", "pro")
    elif event_type == "invoice.paid":
        subscription_status = "active"
    elif event_type == "invoice.payment_failed":
        subscription_status = "past_due"
    elif event_type == "customer.subscription.deleted":
        subscription_status = "canceled"
        tier = "free"
    else:
        return {"handled": False, "reason": "unhandled_event_type"}

    values = {}
    if subscription_status:
        values["subscription_status"] = subscription_status
    if tier:
        values["tier"] = tier
    if values:
        async with db_session.begin():
            stmt = (
                update(Tenant)
                .where(Tenant.stripe_customer_id == customer_id)
                .values(**values)
            )
            await db_session.execute(stmt)

    return {"handled": True, "event": event_type, "customer_id": customer_id}
