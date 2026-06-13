"""Analytics lakehouse package — requires the 'analytics' optional extra."""

from __future__ import annotations

_MISSING: list[str] = []

try:
    import pyarrow  # noqa: F401
except ImportError:
    _MISSING.append("pyarrow")

try:
    import duckdb  # noqa: F401
except ImportError:
    _MISSING.append("duckdb")

try:
    import boto3  # noqa: F401
except ImportError:
    _MISSING.append("boto3")


def require_analytics() -> None:
    """Raise a clear error when optional analytics dependencies are missing."""
    if _MISSING:
        raise RuntimeError(
            f"Analytics dependencies not installed: {', '.join(_MISSING)}. "
            "Run: pip install 'humetric[analytics]'"
        )
