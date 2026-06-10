"""Anthropic istemcisi + tool-use ile yapilandirilmis cikti yardimcisi."""

from __future__ import annotations

import time
from typing import Type, TypeVar

import anthropic
from pydantic import BaseModel

from .. import config
from .. import telemetry

_client: anthropic.Anthropic | None = None
_byo_client_cache: dict[str, anthropic.Anthropic] = {}
T = TypeVar("T", bound=BaseModel)


def _get_client(api_key: str | None = None) -> anthropic.Anthropic:
    global _client, _byo_client_cache
    # Treat an empty/whitespace key like None: fall through to the platform
    # singleton, which raises a clear "missing key" error via require_keys()
    # instead of constructing a client with an unusable empty credential.
    if api_key and api_key.strip():
        if api_key not in _byo_client_cache:
            _byo_client_cache[api_key] = anthropic.Anthropic(
                api_key=api_key,
                max_retries=config.LLM_MAX_RETRIES,
            )
        return _byo_client_cache[api_key]
    if _client is None:
        config.require_keys()
        _client = anthropic.Anthropic(
            api_key=config.ANTHROPIC_API_KEY,
            max_retries=config.LLM_MAX_RETRIES,
        )
    return _client


async def get_tenant_llm_key(tenant_id: int, db) -> str:
    """Tenant BYO Anthropic key'i varsa onu, yoksa platform key'ini dondurur."""
    from ..store import Store
    try:
        tenant_key = await Store.decrypt_tenant_key(db, tenant_id, "anthropic")
    except Exception:
        tenant_key = None
    return tenant_key or config.ANTHROPIC_API_KEY


async def structured_call(
    *,
    model: str,
    system: str,
    user: str | list[dict],
    schema: Type[T],
    tool_name: str,
    tool_description: str,
    api_key: str | None = None,
    tenant_id: int | None = None,
    call_meta: dict | None = None,
) -> T:
    import asyncio
    import logging

    _log = logging.getLogger(__name__)

    tool: dict = {
        "name": tool_name,
        "description": tool_description,
        "input_schema": schema.model_json_schema(),
    }
    if config.PROMPT_CACHE_ENABLED:
        system_param: object = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]
        tool["cache_control"] = {"type": "ephemeral"}
    else:
        system_param = system

    t0 = time.perf_counter()
    client = _get_client(api_key=api_key)

    try:
        resp = await asyncio.to_thread(
            lambda: client.messages.create(
                model=model,
                max_tokens=config.MAX_TOKENS,
                system=system_param,
                tools=[tool],
                tool_choice={"type": "tool", "name": tool_name},
                messages=[{"role": "user", "content": user if isinstance(user, list) else user}],
            )
        )
    except anthropic.BadRequestError as exc:
        _log.warning("Non-retryable LLM error (400): %s", exc)
        raise
    except (anthropic.APIStatusError, anthropic.RateLimitError, anthropic.APIConnectionError) as exc:
        status_code = getattr(exc, "status_code", None)
        _log.error("LLM call failed (status=%s): %s", status_code, exc)
        raise

    latency_ms = int((time.perf_counter() - t0) * 1000)
    total_tokens = (resp.usage.input_tokens if resp.usage else 0) + (resp.usage.output_tokens if resp.usage else 0)
    telemetry.log_call(agent=tool_name, model=model, usage=resp.usage, latency_ms=latency_ms)

    if tenant_id is not None:
        try:
            from ..services.usage_service import record_llm_tokens
            if total_tokens > 0:
                await record_llm_tokens(tenant_id, total_tokens)
        except Exception:
            _log.exception("Failed to record LLM tokens for tenant %d", tenant_id)

    for blok in resp.content:
        if blok.type == "tool_use" and blok.name == tool_name:
            if call_meta is not None:
                from .versioning import hash_prompt, hash_schema
                system_text = system if isinstance(system, str) else str(system)
                call_meta["prompt_hash"] = hash_prompt(system_text)
                call_meta["schema_hash"] = hash_schema(schema)
                call_meta["model"] = model
            return schema.model_validate(blok.input)
    raise RuntimeError(f"Model '{tool_name}' aracini cagirmadi. Yanit: {resp.content!r}")
