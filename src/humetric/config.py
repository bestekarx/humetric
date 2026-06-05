"""Merkezi yapilandirma: database URL, API key'ler, embedding ayarlari."""

from __future__ import annotations

import math
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[3]
LOGS_DIR = ROOT / "logs"

DATABASE_URL = os.environ.get("DATABASE_URL", "")
DATABASE_URL_APP = os.environ.get("DATABASE_URL_APP", "") or DATABASE_URL
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY", "")
AUTH_SECRET = os.environ.get("HUMETRIC_AUTH_SECRET", "")
HUMETRIC_ENCRYPTION_KEY = os.environ.get("HUMETRIC_ENCRYPTION_KEY", "")

EMBED_MODEL = os.environ.get("HUMETRIC_EMBED_MODEL", "voyage-3")
EMBED_DIM = int(os.environ.get("HUMETRIC_EMBED_DIM", "1024"))
EMBED_BACKOFF_S = float(os.environ.get("HUMETRIC_EMBED_BACKOFF_S", "22"))
EMBED_RETRIES = int(os.environ.get("HUMETRIC_EMBED_RETRIES", "6"))

EMBEDDING_PROVIDER = os.environ.get("HUMETRIC_EMBEDDING_PROVIDER", "voyage")
OPENAI_API_KEY = os.environ.get("HUMETRIC_OPENAI_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
COHERE_API_KEY = os.environ.get("HUMETRIC_COHERE_API_KEY", "") or os.environ.get("COHERE_API_KEY", "")
EMBED_DIM_VOYAGE = int(os.environ.get("HUMETRIC_EMBED_DIM_VOYAGE", "1024"))
EMBED_DIM_OPENAI = int(os.environ.get("HUMETRIC_EMBED_DIM_OPENAI", "1536"))
EMBED_DIM_COHERE = int(os.environ.get("HUMETRIC_EMBED_DIM_COHERE", "1024"))

WORKER_POLL_INTERVAL_S = float(os.environ.get("HUMETRIC_WORKER_POLL_INTERVAL_S", "1"))
WORKER_BATCH_SIZE = int(os.environ.get("HUMETRIC_WORKER_BATCH_SIZE", "5"))
TASK_MAX_RETRIES = int(os.environ.get("HUMETRIC_TASK_MAX_RETRIES", "3"))

AGENT_MODEL = os.environ.get("HUMETRIC_AGENT_MODEL", "claude-haiku-4-5-20251001")
MATCHMAKER_MODEL = os.environ.get("HUMETRIC_MATCHMAKER_MODEL", "claude-sonnet-4-6")
CURATOR_MODEL = os.environ.get("HUMETRIC_CURATOR_MODEL", "claude-sonnet-4-6")
WIZARD_MODEL = os.environ.get("HUMETRIC_WIZARD_MODEL", "claude-haiku-4-5-20251001")

TOP_K = int(os.environ.get("HUMETRIC_TOP_K", "10"))
RETRIEVE_K = int(os.environ.get("HUMETRIC_RETRIEVE_K", "50"))
LLM_K = int(os.environ.get("HUMETRIC_LLM_K", "20"))
HYBRID_VECTOR_WEIGHT = float(os.environ.get("HUMETRIC_HYBRID_VECTOR_WEIGHT", "0.7"))
HYBRID_TEXT_WEIGHT = float(os.environ.get("HUMETRIC_HYBRID_TEXT_WEIGHT", "0.3"))
GUVEN_ESIGI = float(os.environ.get("HUMETRIC_GUVEN_ESIGI", "0.55"))
MAX_TOKENS = int(os.environ.get("HUMETRIC_MAX_TOKENS", "2048"))
DECAY_LAMBDA = float(os.environ.get("HUMETRIC_DECAY_LAMBDA", str(math.log(2) / 365)))
PROMPT_CACHE_ENABLED = os.environ.get("HUMETRIC_PROMPT_CACHE_ENABLED", "true").lower() != "false"

API_PORT = int(os.environ.get("HUMETRIC_API_PORT", "8002"))

TELEMETRY_PATH = Path(os.environ.get("HUMETRIC_TELEMETRY_PATH", str(LOGS_DIR / "agent_calls.jsonl")))

HASSAS_METRIC_KEYS: list[str] = os.environ.get(
    "HUMETRIC_HASSAS_METRIC_KEYS",
    "",
).split(",") if os.environ.get("HUMETRIC_HASSAS_METRIC_KEYS") else []

RATE_LIMIT_PER_MINUTE = int(os.environ.get("HUMETRIC_RATE_LIMIT", "100"))

# Stripe (Spec 026)
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRO_MONTHLY_PRICE_ID = os.environ.get("STRIPE_PRO_MONTHLY_PRICE_ID", "")
STRIPE_ENTERPRISE_MONTHLY_PRICE_ID = os.environ.get("STRIPE_ENTERPRISE_MONTHLY_PRICE_ID", "")

# Email (Spec 026)
SMTP_HOST = os.environ.get("SMTP_HOST", "localhost")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "25"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "noreply@humetric.io")

# Captcha (Spec 026)
CAPTCHA_SITE_KEY = os.environ.get("CAPTCHA_SITE_KEY", "")
CAPTCHA_SECRET_KEY = os.environ.get("CAPTCHA_SECRET_KEY", "")

# HuMetric base URL (Spec 026)
HUMETRIC_BASE_URL = os.environ.get("HUMETRIC_BASE_URL", "http://localhost:8002")

# MCP (Spec 026)
HUMETRIC_MCP_API_KEY = os.environ.get("HUMETRIC_MCP_API_KEY", "")

# Self-service kayit (Spec 026)
REGISTER_RATE_LIMIT_PER_HOUR = int(os.environ.get("HUMETRIC_REGISTER_RATE_LIMIT", "3"))
FREE_TIER_SIGNAL_LIMIT = int(os.environ.get("HUMETRIC_FREE_TIER_SIGNAL_LIMIT", "1000"))
FREE_TIER_ENTITY_LIMIT = int(os.environ.get("HUMETRIC_FREE_TIER_ENTITY_LIMIT", "10"))
FREE_TIER_PACK_LIMIT = int(os.environ.get("HUMETRIC_FREE_TIER_PACK_LIMIT", "1"))


def require_keys() -> None:
    """API key'lerin varligini kontrol et, eksikse hata ver."""
    eksik = [
        ad
        for ad, deger in (
            ("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY),
            ("VOYAGE_API_KEY", VOYAGE_API_KEY),
        )
        if not deger
    ]
    if eksik:
        raise RuntimeError(
            f"Eksik API key: {', '.join(eksik)}. .env dosyasini doldurun (.env.example'a bakin)."
        )


def require_db() -> None:
    """DB baglantisi gerektiren komutlardan once cagir."""
    eksik = [
        ad
        for ad, deger in (
            ("DATABASE_URL", DATABASE_URL),
            ("HUMETRIC_AUTH_SECRET", AUTH_SECRET),
        )
        if not deger
    ]
    if eksik:
        raise RuntimeError(
            f"Eksik DB/auth config: {', '.join(eksik)}. .env dosyasini doldurun."
        )
