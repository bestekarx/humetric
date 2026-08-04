"""BYOK beta-lock + bug-fix tests.

Pure unit tests — no live DB required. They exercise:
  * TenantKeysUpdate.llm_provider validation (Bug #1 / beta lock)
  * get_tenant_llm_config's disabled-provider safety net (Bug #2/#4)

The DB-backed delete_tenant_keys reset is covered end-to-end by the
``beta_smoke`` scenario in humetric_test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from humetric import config
from humetric.schema import TenantKeysUpdate


# ── Bug #1 / beta lock: llm_provider validation ─────────────────────────────

def test_llm_provider_openai_rejected_under_beta_lock():
    # Default ENABLED_LLM_PROVIDERS is ["anthropic"], so a valid-but-disabled
    # provider must be rejected at the schema layer (FastAPI → 422).
    assert config.ENABLED_LLM_PROVIDERS == ["anthropic"]
    with pytest.raises(ValidationError):
        TenantKeysUpdate(llm_provider="openai")


def test_llm_provider_garbage_rejected():
    with pytest.raises(ValidationError):
        TenantKeysUpdate(llm_provider="garbage")


def test_llm_provider_anthropic_accepted():
    m = TenantKeysUpdate(llm_provider="anthropic")
    assert m.llm_provider == "anthropic"


def test_llm_provider_none_accepted():
    # None means "don't change the provider" — must pass validation.
    m = TenantKeysUpdate(anthropic_key="sk-test", llm_provider=None)
    assert m.llm_provider is None


def test_llm_provider_respects_env_expansion(monkeypatch):
    # Simulate post-beta: expanding the enabled set should accept openai.
    monkeypatch.setattr(config, "ENABLED_LLM_PROVIDERS", ["anthropic", "openai"])
    assert TenantKeysUpdate(llm_provider="openai").llm_provider == "openai"
    with pytest.raises(ValidationError):
        TenantKeysUpdate(llm_provider="google")


# ── Bug #2/#4: get_tenant_llm_config safety net ─────────────────────────────

@pytest.mark.asyncio
async def test_get_tenant_llm_config_disabled_provider_falls_back(monkeypatch):
    """A stale/disabled provider in the DB must never break the pipeline —
    it falls back to anthropic + the platform key."""
    from humetric.agents import base
    from humetric.store import Store

    async def fake_get_tenant_keys(db, tenant_id):
        return {"llm_provider": "openai"}  # disabled under beta lock

    async def fake_decrypt(db, tenant_id, key_type):
        return None  # tenant has no key for the disabled provider

    monkeypatch.setattr(Store, "get_tenant_keys", staticmethod(fake_get_tenant_keys))
    monkeypatch.setattr(Store, "decrypt_tenant_key", staticmethod(fake_decrypt))
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-platform")

    provider, api_key = await base.get_tenant_llm_config(tenant_id=1, db=None)

    assert provider == "anthropic"
    assert api_key == "sk-platform"


@pytest.mark.asyncio
async def test_get_tenant_llm_config_anthropic_byo_key(monkeypatch):
    """When anthropic is selected and a BYO key exists, it is returned as-is."""
    from humetric.agents import base
    from humetric.store import Store

    async def fake_get_tenant_keys(db, tenant_id):
        return {"llm_provider": "anthropic"}

    async def fake_decrypt(db, tenant_id, key_type):
        return "sk-byo" if key_type == "anthropic" else None

    monkeypatch.setattr(Store, "get_tenant_keys", staticmethod(fake_get_tenant_keys))
    monkeypatch.setattr(Store, "decrypt_tenant_key", staticmethod(fake_decrypt))

    provider, api_key = await base.get_tenant_llm_config(tenant_id=1, db=None)

    assert provider == "anthropic"
    assert api_key == "sk-byo"
