"""Email service — verification, welcome, limit warning, export emails (Spec 026).

Every message is rendered by ``_render`` into the same editorial shell so the
product looks like itself in an inbox. HTML mail is not the web: clients strip
``<style>`` blocks, CSS variables and web fonts, so the palette is inlined as
literal hex and the layout is a nested table rather than flexbox.
"""

from __future__ import annotations

import logging
import smtplib
from email import encoders
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from html import escape
from pathlib import Path

from ..config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, HUMETRIC_BASE_URL

logger = logging.getLogger("humetric.email")

# Mirrors humetric-site's backend/src/mailer.ts, which is the house email style:
# 480px centred card, "Hu" + italic rust "Metric" wordmark, oatmeal highlight
# box, rust button. Kept as literal hex because no mail client resolves var().
_INK = "#15130f"
_PAPER = "#f4efe6"
_CARD = "#fbf8f1"
_OAT = "#ece5d6"
_LINE = "#2a2620"
_RUST = "#b5421f"
_MUTE = "#6b6355"
_HAIR = "rgba(42,38,32,0.22)"

_SERIF = "Georgia, serif"
_SANS = "'Spline Sans', Arial, sans-serif"
_MONO = "'Spline Sans Mono', monospace"


def _render(
    *,
    heading: str,
    intro: str,
    highlight: tuple[str | None, str] | None = None,
    button: tuple[str, str] | None = None,
    footnote: str | None = None,
) -> tuple[str, str]:
    """Render one message as (html, plain_text).

    ``highlight`` is the oatmeal box: ``(label, value)`` prints a small caps
    label above a mono value, ``(None, value)`` prints the value alone in the
    large letter-spaced style the reset-password code uses.

    Both halves come from the same arguments so they cannot drift apart. The
    plain-text alternative is not decoration: an HTML-only message scores worse
    with spam filters and is unreadable in text-only clients.
    """
    rows = f"""
          <tr>
            <td style="padding:32px 32px 8px;text-align:center;">
              <div style="font-family:{_SERIF};font-size:24px;font-weight:900;letter-spacing:-0.02em;">
                Hu<span style="color:{_RUST};font-style:italic;">Metric</span>
              </div>
            </td>
          </tr>
          <tr>
            <td style="padding:12px 32px 0;text-align:center;">
              <h1 style="font-family:{_SERIF};font-size:20px;font-weight:700;margin:0 0 8px;">{escape(heading)}</h1>
              <p style="font-size:13px;color:{_MUTE};margin:0;line-height:1.6;">
                {escape(intro)}
              </p>
            </td>
          </tr>"""

    if highlight is not None:
        label, value = highlight
        if label is None:
            inner = (
                f'<div style="font-family:{_MONO};font-size:26px;letter-spacing:0.3em;'
                f'font-weight:700;color:{_INK};">{escape(value)}</div>'
            )
        else:
            inner = (
                f'<div style="font-size:11px;color:{_MUTE};letter-spacing:0.08em;'
                f'text-transform:uppercase;margin-bottom:6px;">{escape(label)}</div>'
                f'<div style="font-family:{_MONO};font-size:15px;font-weight:700;'
                f'color:{_INK};word-break:break-all;">{escape(value)}</div>'
            )
        rows += f"""
          <tr>
            <td style="padding:24px 32px 0;text-align:center;">
              <div style="display:inline-block;background:{_OAT};border:1px solid {_HAIR};border-radius:8px;padding:14px 24px;">
                {inner}
              </div>
            </td>
          </tr>"""

    if button is not None:
        blabel, burl = button
        rows += f"""
          <tr>
            <td style="padding:22px 32px 0;text-align:center;">
              <a href="{escape(burl, quote=True)}" style="display:inline-block;background:{_RUST};color:{_CARD};text-decoration:none;font-size:13px;font-weight:600;padding:12px 28px;border-radius:7px;">
                {escape(blabel)}
              </a>
            </td>
          </tr>"""

    rows += f"""
          <tr>
            <td style="padding:20px 32px 32px;text-align:center;">
              <p style="font-size:11px;color:{_MUTE};line-height:1.6;margin:0;">
                {escape(footnote) if footnote else ""}
              </p>
            </td>
          </tr>"""

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="color-scheme" content="light only"></head>
<body style="margin:0;padding:0;background:{_PAPER};font-family:{_SANS};color:{_INK};">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;">{escape(intro)}</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_PAPER};padding:40px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="max-width:480px;width:100%;background:{_CARD};border:1.5px solid {_LINE};border-radius:12px;overflow:hidden;">{rows}
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    lines = [heading, "", intro, ""]
    if highlight is not None:
        label, value = highlight
        lines += [f"{label}: {value}" if label else value, ""]
    if button is not None:
        lines += [f"{button[0]}: {button[1]}", ""]
    if footnote:
        lines += [footnote, ""]
    lines += ["--", f"HuMetric · {HUMETRIC_BASE_URL}"]
    return html, "\n".join(lines)


def _smtp_unconfigured() -> bool:
    return not SMTP_HOST or (SMTP_HOST == "localhost" and SMTP_PORT == 25)


def _deliver(msg: MIMEMultipart, to_email: str) -> None:
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        if SMTP_USER:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM, to_email, msg.as_string())


async def send_email(to_email: str, subject: str, html_body: str, text_body: str | None = None) -> bool:
    if _smtp_unconfigured():
        logger.info("SMTP not configured, printing email to log:\nTo: %s\nSubject: %s\n%s", to_email, subject, html_body)
        return True
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = SMTP_FROM
        msg["To"] = to_email
        msg["Subject"] = subject
        # Least-preferred part first: clients render the last one they understand.
        msg.attach(MIMEText(text_body or "", "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        _deliver(msg, to_email)
        return True
    except Exception:
        logger.exception("Failed to send email to %s", to_email)
        return False


async def send_email_with_attachment(
    to_email: str,
    subject: str,
    html_body: str,
    attachment_path: Path,
    attachment_filename: str,
    text_body: str | None = None,
) -> bool:
    if _smtp_unconfigured():
        logger.info(
            "SMTP not configured, would send email to %s with attachment %s:\nSubject: %s\n%s",
            to_email, attachment_filename, subject, html_body,
        )
        return True
    try:
        msg = MIMEMultipart("mixed")
        msg["From"] = SMTP_FROM
        msg["To"] = to_email
        msg["Subject"] = subject

        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(text_body or "", "plain", "utf-8"))
        alt.attach(MIMEText(html_body, "html", "utf-8"))
        msg.attach(alt)

        with open(attachment_path, "rb") as f:
            part = MIMEBase("application", "zip")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{attachment_filename}"')
        msg.attach(part)

        _deliver(msg, to_email)
        return True
    except Exception:
        logger.exception("Failed to send email with attachment to %s", to_email)
        return False


async def send_verification_email(to_email: str, token: str) -> bool:
    verify_url = f"{HUMETRIC_BASE_URL}/v1/verify-email?token={token}"
    html, text = _render(
        heading="Verify your email address",
        intro="Welcome to HuMetric. Confirm this address to activate your account "
              "and start turning signals into entity metrics.",
        button=("Verify email address", verify_url),
        footnote="This link expires in 24 hours. If you didn't create a HuMetric "
                 "account, you can safely ignore this email.",
    )
    return await send_email(to_email, "HuMetric — Email Verification", html, text)


async def send_welcome_email(to_email: str) -> bool:
    html, text = _render(
        heading="Your account is verified",
        intro="Your email address is confirmed and your account is active. Create "
              "an API key from the dashboard to make your first request.",
        highlight=("Documentation", f"{HUMETRIC_BASE_URL}/docs"),
        button=("Open dashboard", f"{HUMETRIC_BASE_URL}/dashboard"),
        footnote="An API key is shown once, at creation time, and cannot be "
                 "retrieved afterwards. Store it somewhere safe.",
    )
    return await send_email(to_email, "HuMetric — Welcome", html, text)


async def send_limit_warning(to_email: str, usage: int, limit: int) -> bool:
    pct = (usage / limit) * 100 if limit > 0 else 0
    html, text = _render(
        heading="You're approaching your usage limit",
        intro="Your tenant has used most of its allowance for the current period. "
              "Raising the plan or waiting for the reset both restore service.",
        highlight=("Usage this period", f"{usage:,} / {limit:,}  ·  {pct:.0f}%"),
        button=("See plans", f"{HUMETRIC_BASE_URL}/pricing"),
        footnote="Once the limit is reached, API requests are rejected until the "
                 "period resets.",
    )
    return await send_email(to_email, f"HuMetric — Usage Warning ({pct:.0f}%)", html, text)


async def send_export_ready_email(
    to_email: str,
    zip_path: Path,
    zip_name: str,
    retention_days: int,
) -> bool:
    html, text = _render(
        heading="Your data export is ready",
        intro="The raw copy of your data — entities, signals, metrics and usage "
              "records — is attached to this email as a zip archive.",
        highlight=("Attached file", zip_name),
        footnote=f"A copy is kept on our servers for {retention_days} days and is "
                 "then deleted permanently. The archive contains your tenant's "
                 "data in full — store it accordingly.",
    )
    return await send_email_with_attachment(
        to_email=to_email,
        subject="Your HuMetric data export is ready",
        html_body=html,
        attachment_path=zip_path,
        attachment_filename=zip_name,
        text_body=text,
    )
