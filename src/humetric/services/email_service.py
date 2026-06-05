"""Email servisi — dogrulama, welcome, limit uyarisi (Spec 026)."""

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
    subject = "HuMetric — Email Dogrulamasi"
    html_body = f"""
    <h2>HuMetric'e Hos Geldiniz!</h2>
    <p>Email adresinizi dogrulamak icin asagidaki linke tiklayin:</p>
    <p><a href="{verify_url}">{verify_url}</a></p>
    <p>Bu link 24 saat gecerlidir.</p>
    """
    return await send_email(to_email, subject, html_body)


async def send_welcome_email(to_email: str, api_key_prefix: str) -> bool:
    subject = "HuMetric — API Key'iniz Hazir"
    html_body = f"""
    <h2>Email Dogrulamasi Tamamlandi!</h2>
    <p>API key'iniz basariyla olusturuldu. Key prefix: <code>{api_key_prefix}</code></p>
    <p>Dokumantasyon: <a href="{HUMETRIC_BASE_URL}/docs">{HUMETRIC_BASE_URL}/docs</a></p>
    <p>Dashboard: <a href="{HUMETRIC_BASE_URL}/dashboard">{HUMETRIC_BASE_URL}/dashboard</a></p>
    """
    return await send_email(to_email, subject, html_body)


async def send_limit_warning(to_email: str, usage: int, limit: int) -> bool:
    pct = (usage / limit) * 100 if limit > 0 else 0
    subject = f"HuMetric — Kullanim Uyarisi (%{pct:.0f})"
    html_body = f"""
    <h2>Kullanim Limitinize Yaklasiyorsunuz</h2>
    <p>Mevcut kullanim: {usage} / {limit} (%{pct:.0f})</p>
    <p>Limit asiminda API istekleriniz reddedilecektir.</p>
    <p>Yukseltmek icin: <a href="{HUMETRIC_BASE_URL}/pricing">Planlar</a></p>
    """
    return await send_email(to_email, subject, html_body)
