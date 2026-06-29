"""Email service — verification, welcome, limit warning emails (Spec 026)."""

from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from ..config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, HUMETRIC_BASE_URL

logger = logging.getLogger("humetric.email")


async def send_email(to_email: str, subject: str, html_body: str) -> bool:
    if not SMTP_HOST or SMTP_HOST == "localhost" and SMTP_PORT == 25:
        logger.info("SMTP not configured, printing email to log:\nTo: %s\nSubject: %s\n%s", to_email, subject, html_body)
        return True
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = SMTP_FROM
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, to_email, msg.as_string())
        return True
    except Exception:
        logger.exception("Failed to send email to %s", to_email)
        return False


async def send_verification_email(to_email: str, token: str) -> bool:
    verify_url = f"{HUMETRIC_BASE_URL}/v1/verify-email?token={token}"
    subject = "HuMetric — Email Verification"
    html_body = f"""
    <h2>Welcome to HuMetric!</h2>
    <p>Click the link below to verify your email address:</p>
    <p><a href="{verify_url}">{verify_url}</a></p>
    <p>This link is valid for 24 hours.</p>
    """
    return await send_email(to_email, subject, html_body)


async def send_welcome_email(to_email: str) -> bool:
    subject = "HuMetric — Welcome"
    html_body = f"""
    <h2>Email Verification Complete!</h2>
    <p>Your account is ready. Create an API key from your dashboard to start
    making requests — the full key is shown once at creation time.</p>
    <p>Dashboard: <a href="{HUMETRIC_BASE_URL}/dashboard">{HUMETRIC_BASE_URL}/dashboard</a></p>
    <p>Documentation: <a href="{HUMETRIC_BASE_URL}/docs">{HUMETRIC_BASE_URL}/docs</a></p>
    """
    return await send_email(to_email, subject, html_body)


async def send_limit_warning(to_email: str, usage: int, limit: int) -> bool:
    pct = (usage / limit) * 100 if limit > 0 else 0
    subject = f"HuMetric — Usage Warning ({pct:.0f}%)"
    html_body = f"""
    <h2>You're Approaching Your Usage Limit</h2>
    <p>Current usage: {usage} / {limit} ({pct:.0f}%)</p>
    <p>Once the limit is exceeded, your API requests will be rejected.</p>
    <p>To upgrade: <a href="{HUMETRIC_BASE_URL}/pricing">Plans</a></p>
    """
    return await send_email(to_email, subject, html_body)
