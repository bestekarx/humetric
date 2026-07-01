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


async def get_tenant_llm_config(tenant_id: int, db) -> tuple[str, str | None]:
    """Return (provider, api_key) for the tenant's active LLM provider.

    Falls back to the platform Anthropic key when the tenant hasn't configured
    a BYO key for the selected provider.
    """
    from ..store import Store
    try:
        keys_dict = await Store.get_tenant_keys(db, tenant_id)
    except Exception:
        return "anthropic", config.ANTHROPIC_API_KEY

    provider = keys_dict.get("llm_provider") or "anthropic"

    # Safety net: a disabled/corrupt provider value in the DB must never break
    # the pipeline. Fall back to anthropic (platform key) — the beta lock keeps
    # only anthropic enabled, so this covers stale non-anthropic rows.
    if provider not in config.ENABLED_LLM_PROVIDERS:
        provider = "anthropic"

    provider_to_store_key = {
        "anthropic": "anthropic",
        "openai": "openai",
        "google": "google",
        "deepseek": "deepseek",
    }
    store_key = provider_to_store_key.get(provider, "anthropic")

    try:
        api_key = await Store.decrypt_tenant_key(db, tenant_id, store_key)
    except Exception:
        api_key = None

    if not api_key and provider == "anthropic":
        api_key = config.ANTHROPIC_API_KEY

    return provider, api_key


def _build_tool_and_system(
    system: str, schema: Type[T], tool_name: str, tool_description: str,
) -> tuple[dict, object]:
    """Build the tool definition and system parameter (with prompt caching when
    enabled). Shared by structured_call and the batch request builder so both
    issue byte-identical tool/system content."""
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
    return tool, system_param


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

    tool, system_param = _build_tool_and_system(system, schema, tool_name, tool_description)

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


# ── Message Batches API (Spec 024 backfill — 50% cost) ─────────────────

def build_batch_request(
    *,
    custom_id: str,
    model: str,
    system: str,
    user: str | list[dict],
    schema: Type[T],
    tool_name: str,
    tool_description: str,
) -> dict:
    """Build a single Message Batches request as a plain dict.

    Mirrors structured_call's request shape (forced tool_use + prompt caching)
    so a batched call produces the same structured output as the sync path.
    The SDK accepts dict params for ``batches.create(requests=...)``.
    """
    tool, system_param = _build_tool_and_system(system, schema, tool_name, tool_description)
    return {
        "custom_id": custom_id,
        "params": {
            "model": model,
            "max_tokens": config.MAX_TOKENS,
            "system": system_param,
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": tool_name},
            "messages": [{"role": "user", "content": user if isinstance(user, list) else user}],
        },
    }


def parse_batch_result(message, schema: Type[T], tool_name: str) -> T:
    """Extract and validate the forced tool_use block from a batched message."""
    for blok in message.content:
        if blok.type == "tool_use" and blok.name == tool_name:
            return schema.model_validate(blok.input)
    raise RuntimeError(
        f"Model '{tool_name}' aracini cagirmadi (batch). Yanit: {message.content!r}"
    )


async def submit_and_await_batch(
    requests: list[dict],
    *,
    api_key: str | None = None,
    poll_interval_s: float | None = None,
) -> dict:
    """Submit a Message Batch, poll until ended, and return a
    ``{custom_id: result}`` map. Each ``result`` carries ``.type``
    ('succeeded'/'errored'/...) and, on success, ``.message``.

    Token usage is recorded post-hoc per succeeded message.
    """
    import asyncio
    import logging

    _log = logging.getLogger(__name__)

    if not requests:
        return {}

    poll = poll_interval_s if poll_interval_s is not None else config.BATCH_POLL_INTERVAL_S
    client = _get_client(api_key=api_key)

    batch = await asyncio.to_thread(lambda: client.messages.batches.create(requests=requests))
    _log.info("Submitted batch %s with %d request(s)", batch.id, len(requests))

    while True:
        info = await asyncio.to_thread(lambda: client.messages.batches.retrieve(batch.id))
        if info.processing_status == "ended":
            break
        await asyncio.sleep(poll)

    items = await asyncio.to_thread(lambda: list(client.messages.batches.results(batch.id)))
    out: dict = {}
    for item in items:
        out[item.custom_id] = item.result
    return out


async def record_batch_usage(messages, tenant_id: int | None) -> None:
    """Record token usage for a list of succeeded batch messages."""
    if tenant_id is None:
        return
    import logging

    _log = logging.getLogger(__name__)
    total = 0
    for msg in messages:
        usage = getattr(msg, "usage", None)
        if usage:
            total += (usage.input_tokens or 0) + (usage.output_tokens or 0)
    if total <= 0:
        return
    try:
        from ..services.usage_service import record_llm_tokens
        await record_llm_tokens(tenant_id, total)
    except Exception:
        _log.exception("Failed to record batch LLM tokens for tenant %d", tenant_id)
