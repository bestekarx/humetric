"""Wizard multi-provider routing testleri — tenant'in llm_provider secimi
generate_pack_yaml'a dogru sekilde ulasmali (bkz. agents/wizard.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from humetric import config
from humetric.agents.wizard import generate_pack_yaml
from humetric.schema import PackDefinition


def _make_pack_definition() -> PackDefinition:
    return PackDefinition(
        entity_type="test_entity",
        label="Test Pack",
        required_fields=["name"],
        metrics=[],
    )


@pytest.mark.parametrize(
    "provider,expected_model",
    [
        ("openai", config.OPENAI_WIZARD_MODEL),
        ("google", config.GOOGLE_WIZARD_MODEL),
        ("deepseek", config.DEEPSEEK_WIZARD_MODEL),
        ("anthropic", config.WIZARD_MODEL),
    ],
)
@pytest.mark.asyncio
async def test_wizard_uses_tenant_provider(provider, expected_model):
    mock_response = _make_pack_definition()

    with patch(
        "humetric.agents.wizard.structured_call_multi",
        new_callable=AsyncMock,
        return_value=mock_response,
    ) as mock_call:
        await generate_pack_yaml("A domain description long enough.", provider=provider, api_key="key")

    mock_call.assert_called_once()
    _, kwargs = mock_call.call_args
    assert kwargs["provider"] == provider
    assert kwargs["model"] == expected_model


@pytest.mark.asyncio
async def test_wizard_defaults_to_anthropic_when_provider_none():
    mock_response = _make_pack_definition()

    with patch(
        "humetric.agents.wizard.structured_call_multi",
        new_callable=AsyncMock,
        return_value=mock_response,
    ) as mock_call:
        await generate_pack_yaml("A domain description long enough.")

    _, kwargs = mock_call.call_args
    assert kwargs["provider"] == "anthropic"
    assert kwargs["model"] == config.WIZARD_MODEL


def test_get_wizard_model_per_provider():
    assert config.get_wizard_model("openai") == config.OPENAI_WIZARD_MODEL
    assert config.get_wizard_model("google") == config.GOOGLE_WIZARD_MODEL
    assert config.get_wizard_model("deepseek") == config.DEEPSEEK_WIZARD_MODEL
    assert config.get_wizard_model("anthropic") == config.WIZARD_MODEL
    assert config.get_wizard_model("unknown") == config.WIZARD_MODEL
