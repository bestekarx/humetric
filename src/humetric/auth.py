"""API key olusturma, hash'leme ve dogrulama.

Key format: hm_live_... / hm_test_...
SHA-256 hash ile saklanir, secrets.token_urlsafe(32) ile uretilir.
"""

from __future__ import annotations

import hashlib
import secrets


def generate_api_key(prefix: str = "hm_test") -> tuple[str, str]:
    """Yeni API key uret: (full_key, key_hash).
    
    full_key: hm_test_8f2c4a1b... (kullaniciya gosterilir, bir daha gorunmez)
    key_hash: SHA-256(full_key) (DB'de saklanir)
    """
    random_part = secrets.token_urlsafe(32)
    full_key = f"{prefix}_{random_part}"
    key_hash = hash_key(full_key)
    return full_key, key_hash


def hash_key(key: str) -> str:
    """API key'i SHA-256 ile hash'le."""
    return hashlib.sha256(key.encode()).hexdigest()


def verify_key(full_key: str, stored_hash: str) -> bool:
    """Gelen API key'in hash'i DB'dekiyle eslesiyor mu?"""
    return hashlib.sha256(full_key.encode()).hexdigest() == stored_hash


def extract_prefix(full_key: str) -> str | None:
    """API key'den prefix'i cikar (hm_live veya hm_test)."""
    if "_" not in full_key:
        return None
    prefix = full_key.split("_")[0]
    if prefix and full_key.startswith(prefix + "_"):
        return prefix
    return None
