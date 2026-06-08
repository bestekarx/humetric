"""API key generation, hashing, and verification.

Key format: hm_live_... / hm_test_...
Stored as a SHA-256 hash, generated with secrets.token_urlsafe(32).
"""

from __future__ import annotations

import hashlib
import secrets


def generate_api_key(prefix: str = "hm_test") -> tuple[str, str]:
    """Generate a new API key: (full_key, key_hash).

    full_key: hm_test_8f2c4a1b... (shown to the user once, never again)
    key_hash: SHA-256(full_key) (stored in the DB)
    """
    random_part = secrets.token_urlsafe(32)
    full_key = f"{prefix}_{random_part}"
    key_hash = hash_key(full_key)
    return full_key, key_hash


def hash_key(key: str) -> str:
    """Hash an API key with SHA-256."""
    return hashlib.sha256(key.encode()).hexdigest()


def verify_key(full_key: str, stored_hash: str) -> bool:
    """Does the incoming API key's hash match the one stored in the DB?"""
    return hashlib.sha256(full_key.encode()).hexdigest() == stored_hash


def extract_prefix(full_key: str) -> str | None:
    """Extract the prefix from an API key (hm_live or hm_test)."""
    if "_" not in full_key:
        return None
    prefix = full_key.split("_")[0]
    if prefix and full_key.startswith(prefix + "_"):
        return prefix
    return None
