"""Captcha servisi — reCAPTCHA v3 / hCaptcha dogrulamasi (Spec 026)."""

from __future__ import annotations

import logging

import httpx

from ..config import CAPTCHA_SECRET_KEY

logger = logging.getLogger("humetric.captcha")

RECAPTCHA_VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"


async def verify_captcha(token: str) -> bool:
    if not CAPTCHA_SECRET_KEY:
        logger.debug("CAPTCHA_SECRET_KEY not configured, bypassing verification")
        return True
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(RECAPTCHA_VERIFY_URL, data={
                "secret": CAPTCHA_SECRET_KEY,
                "response": token,
            })
            result = resp.json()
            success = result.get("success", False)
            if not success:
                logger.warning("Captcha failed: %s", result.get("error-codes", []))
            return success
    except Exception:
        logger.exception("Captcha verification error")
        return False
