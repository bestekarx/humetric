"""BYOK provider-selection + bug-fix tests.

Pure unit tests — no live DB required. They exercise:
  * TenantKeysUpdate.llm_provider validation (Bug #1 / enabled-provider gate)
  * get_tenant_llm_config's disabled-provider safety net (Bug #2/#4)

The DB-backed delete_tenant_keys reset is covered end-to-end by the
``beta_smoke`` scenario in humetric_test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from humetric import config
from humetric.schema import TenantKeysUpdate


# ── Bug #1 / enabled-provider gate: llm_provider validation ─────────────────

def test_all_four_providers_enabled_by_default():
    # Tenants pick their provider in the dashboard; all four ship enabled.
    assert config.ENABLED_LLM_PROVIDERS == ["anthropic", "openai", "google", "deepseek"]
    for provider in ("anthropic", "openai", "google", "deepseek"):
        assert TenantKeysUpdate(llm_provider=provider).llm_provider == provider


def test_llm_provider_rejected_when_disabled(monkeypatch):
    # A deployment that narrows the enabled set must reject the others at the
    # schema layer (FastAPI → 422), not silently accept and fail at call time.
    monkeypatch.setattr(config, "ENABLED_LLM_PROVIDERS", ["anthropic"])
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


def test_llm_provider_respects_env_narrowing(monkeypatch):
    # Narrowing the enabled set must take effect immediately.
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
        return {"llm_provider": "openai"}  # not in the enabled set below

    async def fake_decrypt(db, tenant_id, key_type):
        return None  # tenant has no key for the disabled provider

    monkeypatch.setattr(config, "ENABLED_LLM_PROVIDERS", ["anthropic"])
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


@pytest.mark.parametrize(
    ("selected", "store_key"),
    [("openai", "openai"), ("google", "google"), ("deepseek", "deepseek")],
)
@pytest.mark.asyncio
async def test_get_tenant_llm_config_routes_to_selected_provider(monkeypatch, selected, store_key):
    """Each tenant's selection routes to that provider and its own BYO key —
    this is what makes provider choice tenant-scoped rather than global."""
    from humetric.agents import base
    from humetric.store import Store

    async def fake_get_tenant_keys(db, tenant_id):
        return {"llm_provider": selected}

    async def fake_decrypt(db, tenant_id, key_type):
        return f"sk-{key_type}" if key_type == store_key else None

    monkeypatch.setattr(Store, "get_tenant_keys", staticmethod(fake_get_tenant_keys))
    monkeypatch.setattr(Store, "decrypt_tenant_key", staticmethod(fake_decrypt))
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-platform")

    provider, api_key = await base.get_tenant_llm_config(tenant_id=1, db=None)

    assert provider == selected
    assert api_key == f"sk-{store_key}"
    # The platform Anthropic key must never leak into another provider's call.
    assert api_key != "sk-platform"
