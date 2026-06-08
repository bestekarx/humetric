"""Stripe billing servisi — Checkout, Customer Portal, Webhook handler (Spec 026)."""

from __future__ import annotations

import logging
from typing import Any

import stripe

from ..config import (
    HUMETRIC_BASE_URL,
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
