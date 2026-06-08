"""Agent cagri telemetrisi — best-effort JSONL loglama.

Saha telemetry.py pattern'inden uyarlanmistir. Hata yutulur, ana is akisi bozulmaz.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config

_lock = threading.Lock()


def log_call(
    *,
    agent: str,
    model: str,
    usage: Any,
    latency_ms: int,
    request_id: str | None = None,
) -> None:
    """Append a single line for the agent call to logs/agent_calls.jsonl. Errors are swallowed."""
    try:
        satir = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent": agent,
            "model": model,
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
            "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_creation": getattr(usage, "cache_creation_input_tokens", 0) or 0,
            "latency_ms": latency_ms,
            "request_id": request_id or "",
        }
        path: Path = config.TELEMETRY_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(satir, ensure_ascii=False) + "\n")
    except Exception:
        pass
