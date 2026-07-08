"""Multi-provider LLM adapter — routes structured calls to Anthropic, OpenAI, Google AI, or DeepSeek."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Type, TypeVar

from pydantic import BaseModel

from .. import config

_log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

SUPPORTED_PROVIDERS = ("anthropic", "openai", "google", "deepseek")


def _schema_injection(system: str, schema: Type[T]) -> str:
    """Append JSON schema instructions to the system prompt for non-Anthropic providers."""
    schema_json = json.dumps(schema.model_json_schema(), indent=2)
    return (
        system
        + "\n\nRespond ONLY with a valid JSON object matching this exact schema "
        "(no markdown, no extra text):\n"
        + schema_json
    )


# ── OpenAI / DeepSeek ──────────────────────────────────────────────────────

_openai_clients: dict[tuple[str, str | None], object] = {}


def _get_openai_client(api_key: str, base_url: str | None = None):
    cache_key = (api_key, base_url)
    if cache_key not in _openai_clients:
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise RuntimeError(
                "The 'openai' package is required for OpenAI/DeepSeek providers. "
                "Install it with: pip install openai>=1.40"
            )
        kwargs = {"api_key": api_key, "max_retries": config.LLM_MAX_RETRIES}
        if base_url:
            kwargs["base_url"] = base_url
        _openai_clients[cache_key] = AsyncOpenAI(**kwargs)
    return _openai_clients[cache_key]


async def _call_openai(
    *,
    model: str,
    api_key: str,
    system: str,
    user: str | list,
    schema: Type[T],
    base_url: str | None = None,
    tenant_id: int | None = None,
) -> T:
    client = _get_openai_client(api_key, base_url)
    user_text = user if isinstance(user, str) else json.dumps(user, ensure_ascii=False)
    enhanced_system = _schema_injection(system, schema)

    t0 = time.perf_counter()
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": enhanced_system},
            {"role": "user", "content": user_text},
        ],
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=config.MAX_TOKENS,
    )
    latency_ms = int((time.perf_counter() - t0) * 1000)

    usage = resp.usage
    total_tokens = (usage.prompt_tokens if usage else 0) + (usage.completion_tokens if usage else 0)
    _log.debug("OpenAI call model=%s tokens=%d latency=%dms", model, total_tokens, latency_ms)

    if tenant_id is not None and total_tokens > 0:
        try:
            from ..services.usage_service import record_llm_tokens
            await record_llm_tokens(tenant_id, total_tokens)
        except Exception:
            _log.exception("Failed to record LLM tokens for tenant %d", tenant_id)

    content = resp.choices[0].message.content
    return schema.model_validate_json(content)


# ── Google AI (Gemini) ─────────────────────────────────────────────────────

# genai.configure() mutates global SDK state, so without a lock concurrent
# requests could leak one tenant's API key into another's call. _google_lock
# serializes configure→generate as a correctness guarantee; it also serializes
# all Google calls, so before opening this provider to heavy traffic migrate
# to the client-based `google-genai` SDK (genai.Client(api_key=...)) where
# each call carries its own credential.
_google_lock = asyncio.Lock()


async def _call_google(
    *,
    model: str,
    api_key: str,
    system: str,
    user: str | list,
    schema: Type[T],
    tenant_id: int | None = None,
) -> T:
    try:
        import google.generativeai as genai
    except ImportError:
        raise RuntimeError(
            "The 'google-generativeai' package is required for Google AI provider. "
            "Install it with: pip install google-generativeai>=0.8"
        )

    user_text = user if isinstance(user, str) else json.dumps(user, ensure_ascii=False)
    enhanced_system = _schema_injection(system, schema)

    t0 = time.perf_counter()
    async with _google_lock:
        genai.configure(api_key=api_key)
        model_obj = genai.GenerativeModel(
            model_name=model,
            system_instruction=enhanced_system,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0,
                max_output_tokens=config.MAX_TOKENS,
            ),
        )
        resp = await asyncio.to_thread(lambda: model_obj.generate_content(user_text))
    latency_ms = int((time.perf_counter() - t0) * 1000)

    total_tokens = 0
    if hasattr(resp, "usage_metadata") and resp.usage_metadata:
        m = resp.usage_metadata
        total_tokens = (getattr(m, "prompt_token_count", 0) or 0) + (
            getattr(m, "candidates_token_count", 0) or 0
        )
    _log.debug("Google AI call model=%s tokens=%d latency=%dms", model, total_tokens, latency_ms)

    if tenant_id is not None and total_tokens > 0:
        try:
            from ..services.usage_service import record_llm_tokens
            await record_llm_tokens(tenant_id, total_tokens)
        except Exception:
            _log.exception("Failed to record LLM tokens for tenant %d", tenant_id)

    return schema.model_validate_json(resp.text)


# ── Public dispatcher ──────────────────────────────────────────────────────

async def structured_call_multi(
    *,
    provider: str,
    model: str,
    api_key: str | None,
    system: str,
    user: str | list,
    schema: Type[T],
    tool_name: str,
    tool_description: str,
    tenant_id: int | None = None,
    call_meta: dict | None = None,
) -> T:
    """Route a structured LLM call to the appropriate provider backend."""
    if not provider or provider == "anthropic":
        from .base import structured_call
        return await structured_call(
            model=model,
            system=system,
            user=user,
            schema=schema,
            tool_name=tool_name,
            tool_description=tool_description,
            api_key=api_key,
            tenant_id=tenant_id,
            call_meta=call_meta,
        )

    if not api_key:
        raise RuntimeError(
            f"No API key configured for provider '{provider}'. "
            "Set your key in the dashboard under API Keys."
        )

    if provider == "openai":
        return await _call_openai(
            model=model,
            api_key=api_key,
            system=system,
            user=user,
            schema=schema,
            tenant_id=tenant_id,
        )

    if provider == "deepseek":
        return await _call_openai(
            model=model,
            api_key=api_key,
            system=system,
            user=user,
            schema=schema,
            base_url="https://api.deepseek.com/v1",
            tenant_id=tenant_id,
        )

    if provider == "google":
        return await _call_google(
            model=model,
            api_key=api_key,
            system=system,
            user=user,
            schema=schema,
            tenant_id=tenant_id,
        )

    raise ValueError(
        f"Unsupported LLM provider: '{provider}'. "
        f"Valid choices: {', '.join(SUPPORTED_PROVIDERS)}"
    )
