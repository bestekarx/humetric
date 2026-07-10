"""Central configuration: database URL, API keys, embedding settings."""

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
# Optional: restrict this worker process to specific task types (comma-separated).
# Default (unset) claims any type. Lets a minutes-long analysis_scan run in a
# separate worker process so it never delays real-time signal processing.
WORKER_TASK_TYPES: list[str] | None = [
    t.strip() for t in os.environ.get("HUMETRIC_WORKER_TASK_TYPES", "").split(",") if t.strip()
] or None
TASK_MAX_RETRIES = int(os.environ.get("HUMETRIC_TASK_MAX_RETRIES", "3"))
WORKER_HEARTBEAT_FILE = os.environ.get("HUMETRIC_WORKER_HEARTBEAT_FILE", "/tmp/humetric_worker_heartbeat")

AGENT_MODEL = os.environ.get("HUMETRIC_AGENT_MODEL", "claude-haiku-4-5-20251001")
MATCHMAKER_MODEL = os.environ.get("HUMETRIC_MATCHMAKER_MODEL", "claude-sonnet-4-6")
CURATOR_MODEL = os.environ.get("HUMETRIC_CURATOR_MODEL", "claude-sonnet-4-6")
WIZARD_MODEL = os.environ.get("HUMETRIC_WIZARD_MODEL", "claude-haiku-4-5-20251001")

# Per-provider model defaults (BYOK multi-provider)
OPENAI_AGENT_MODEL = os.environ.get("HUMETRIC_OPENAI_AGENT_MODEL", "gpt-4o-mini")
OPENAI_CURATOR_MODEL = os.environ.get("HUMETRIC_OPENAI_CURATOR_MODEL", "gpt-4o")
OPENAI_RANKER_MODEL = os.environ.get("HUMETRIC_OPENAI_RANKER_MODEL", "gpt-4o")
GOOGLE_AGENT_MODEL = os.environ.get("HUMETRIC_GOOGLE_AGENT_MODEL", "gemini-1.5-flash")
GOOGLE_CURATOR_MODEL = os.environ.get("HUMETRIC_GOOGLE_CURATOR_MODEL", "gemini-1.5-pro")
GOOGLE_RANKER_MODEL = os.environ.get("HUMETRIC_GOOGLE_RANKER_MODEL", "gemini-1.5-pro")
DEEPSEEK_AGENT_MODEL = os.environ.get("HUMETRIC_DEEPSEEK_AGENT_MODEL", "deepseek-chat")
DEEPSEEK_CURATOR_MODEL = os.environ.get("HUMETRIC_DEEPSEEK_CURATOR_MODEL", "deepseek-chat")
DEEPSEEK_RANKER_MODEL = os.environ.get("HUMETRIC_DEEPSEEK_RANKER_MODEL", "deepseek-chat")

# BYOK: beta allows anthropic only; expand with comma-separated list.
# To enable all 4 providers with a single env var:
# HUMETRIC_ENABLED_LLM_PROVIDERS=anthropic,openai,google,deepseek
ENABLED_LLM_PROVIDERS = [
    p.strip()
    for p in os.environ.get("HUMETRIC_ENABLED_LLM_PROVIDERS", "anthropic").split(",")
    if p.strip()
]


def get_extractor_model(provider: str) -> str:
    if provider == "openai":
        return OPENAI_AGENT_MODEL
    if provider == "google":
        return GOOGLE_AGENT_MODEL
    if provider == "deepseek":
        return DEEPSEEK_AGENT_MODEL
    return AGENT_MODEL


def get_curator_model(provider: str) -> str:
    if provider == "openai":
        return OPENAI_CURATOR_MODEL
    if provider == "google":
        return GOOGLE_CURATOR_MODEL
    if provider == "deepseek":
        return DEEPSEEK_CURATOR_MODEL
    return CURATOR_MODEL


def get_ranker_model(provider: str) -> str:
    if provider == "openai":
        return OPENAI_RANKER_MODEL
    if provider == "google":
        return GOOGLE_RANKER_MODEL
    if provider == "deepseek":
        return DEEPSEEK_RANKER_MODEL
    return MATCHMAKER_MODEL

TOP_K = int(os.environ.get("HUMETRIC_TOP_K", "10"))
RETRIEVE_K = int(os.environ.get("HUMETRIC_RETRIEVE_K", "50"))
LLM_K = int(os.environ.get("HUMETRIC_LLM_K", "20"))
HYBRID_VECTOR_WEIGHT = float(os.environ.get("HUMETRIC_HYBRID_VECTOR_WEIGHT", "0.7"))
HYBRID_TEXT_WEIGHT = float(os.environ.get("HUMETRIC_HYBRID_TEXT_WEIGHT", "0.3"))
CONFIDENCE_THRESHOLD = float(os.environ.get("HUMETRIC_CONFIDENCE_THRESHOLD", "0.55"))
MAX_TOKENS = int(os.environ.get("HUMETRIC_MAX_TOKENS", "2048"))
DECAY_LAMBDA = float(os.environ.get("HUMETRIC_DECAY_LAMBDA", str(math.log(2) / 365)))
PROMPT_CACHE_ENABLED = os.environ.get("HUMETRIC_PROMPT_CACHE_ENABLED", "true").lower() != "false"

# Cost controls (backfill). Curator fast-path: on a cold-start entity (no
# existing metrics) the Sonnet curator is a near-deterministic pass-through,
# so finalize the extractor output locally and skip the LLM call. Opt-in —
# default off keeps real-time behaviour unchanged.
CURATOR_FAST_PATH_ENABLED = (
    os.environ.get("HUMETRIC_CURATOR_FAST_PATH_ENABLED", "false").lower() == "true"
)
# Batch worker: drains the signal_process queue via the Anthropic Message
# Batches API (50% cost) for backfill. Tunables for the one-shot batch job.
BATCH_SUBMIT_SIZE = int(os.environ.get("HUMETRIC_BATCH_SUBMIT_SIZE", "1000"))
BATCH_POLL_INTERVAL_S = float(os.environ.get("HUMETRIC_BATCH_POLL_INTERVAL_S", "30"))
BATCH_RECLAIM_S = float(os.environ.get("HUMETRIC_BATCH_RECLAIM_S", "3600"))

API_PORT = int(os.environ.get("HUMETRIC_API_PORT", "8002"))

TELEMETRY_PATH = Path(os.environ.get("HUMETRIC_TELEMETRY_PATH", str(LOGS_DIR / "agent_calls.jsonl")))

HASSAS_METRIC_KEYS: list[str] = os.environ.get(
    "HUMETRIC_HASSAS_METRIC_KEYS",
    "",
).split(",") if os.environ.get("HUMETRIC_HASSAS_METRIC_KEYS") else []

RATE_LIMIT_PER_MINUTE = int(os.environ.get("HUMETRIC_RATE_LIMIT", "100"))

LLM_MAX_RETRIES = int(os.environ.get("HUMETRIC_LLM_MAX_RETRIES", "3"))
DECAY_ENABLED = os.environ.get("HUMETRIC_DECAY_ENABLED", "true").lower() != "false"
ENFORCE_TIER_LIMITS = os.environ.get("HUMETRIC_ENFORCE_TIER_LIMITS", "false").lower() == "true"

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

# CORS: comma-separated allowed origins. Defaults to "*" for local dev; set to
# the dashboard origin(s) in production to prevent cross-origin credential use.
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("HUMETRIC_CORS_ALLOWED_ORIGINS", "*").split(",")
    if origin.strip()
]

# MCP (Spec 026)
HUMETRIC_MCP_API_KEY = os.environ.get("HUMETRIC_MCP_API_KEY", "")

# Self-service registration (Spec 026)
REGISTER_RATE_LIMIT_PER_HOUR = int(os.environ.get("HUMETRIC_REGISTER_RATE_LIMIT", "3"))
# When false, /register auto-verifies the tenant and returns the API key
# immediately (no email step). Defaults to requiring verification.
REQUIRE_EMAIL_VERIFICATION = (
    os.environ.get("HUMETRIC_REQUIRE_EMAIL_VERIFICATION", "true").lower() != "false"
)
FREE_TIER_SIGNAL_LIMIT = int(os.environ.get("HUMETRIC_FREE_TIER_SIGNAL_LIMIT", "1000"))
FREE_TIER_ENTITY_LIMIT = int(os.environ.get("HUMETRIC_FREE_TIER_ENTITY_LIMIT", "10"))
FREE_TIER_PACK_LIMIT = int(os.environ.get("HUMETRIC_FREE_TIER_PACK_LIMIT", "1"))


# Analytics lakehouse export
EXPORT_ENABLED = os.environ.get("HUMETRIC_EXPORT_ENABLED", "false").lower() == "true"
EXPORT_STORAGE = os.environ.get("HUMETRIC_EXPORT_STORAGE", "local")  # local | s3
EXPORT_LOCAL_DIR = Path(os.environ.get("HUMETRIC_EXPORT_LOCAL_DIR", str(ROOT / "lakehouse")))
EXPORT_S3_BUCKET = os.environ.get("HUMETRIC_EXPORT_S3_BUCKET", "")
EXPORT_S3_PREFIX = os.environ.get("HUMETRIC_EXPORT_S3_PREFIX", "")
EXPORT_S3_ENDPOINT_URL = os.environ.get("HUMETRIC_EXPORT_S3_ENDPOINT_URL", "")
EXPORT_S3_REGION = os.environ.get("HUMETRIC_EXPORT_S3_REGION", "auto")
EXPORT_S3_ACCESS_KEY_ID = (
    os.environ.get("HUMETRIC_EXPORT_S3_ACCESS_KEY_ID", "")
    or os.environ.get("AWS_ACCESS_KEY_ID", "")
)
EXPORT_S3_SECRET_ACCESS_KEY = (
    os.environ.get("HUMETRIC_EXPORT_S3_SECRET_ACCESS_KEY", "")
    or os.environ.get("AWS_SECRET_ACCESS_KEY", "")
)
EXPORT_HOUR_UTC = int(os.environ.get("HUMETRIC_EXPORT_HOUR_UTC", "2"))
EXPORT_SCHEDULER_INTERVAL_S = float(os.environ.get("HUMETRIC_EXPORT_SCHEDULER_INTERVAL_S", "300"))


def require_keys() -> None:
    """Check that the required API keys are present; raise if any are missing."""
    missing = [
        name
        for name, value in (
            ("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY),
            ("VOYAGE_API_KEY", VOYAGE_API_KEY),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Missing API key(s): {', '.join(missing)}. Fill in your .env file (see .env.example)."
        )


def require_db() -> None:
    """Call this before any command that requires a DB connection."""
    missing = [
        name
        for name, value in (
            ("DATABASE_URL", DATABASE_URL),
            ("HUMETRIC_AUTH_SECRET", AUTH_SECRET),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Missing DB/auth config: {', '.join(missing)}. Fill in your .env file."
        )
