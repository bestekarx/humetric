"""Anthropic istemcisi + tool-use ile yapilandirilmis cikti yardimcisi."""

from __future__ import annotations

import time
from typing import Type, TypeVar

import anthropic
from pydantic import BaseModel

from .. import config
from .. import telemetry

_client: anthropic.Anthropic | None = None
T = TypeVar("T", bound=BaseModel)


def _get_client(api_key: str | None = None) -> anthropic.Anthropic:
    global _client
    if api_key is not None:
        return anthropic.Anthropic(api_key=api_key)
    if _client is None:
        config.require_keys()
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
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
    tool_ad: str,
    tool_aciklama: str,
    api_key: str | None = None,
) -> T:
    import asyncio
    tool: dict = {
        "name": tool_ad,
        "description": tool_aciklama,
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
    resp = await asyncio.to_thread(
        lambda: client.messages.create(
            model=model,
            max_tokens=config.MAX_TOKENS,
            system=system_param,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool_ad},
            messages=[{"role": "user", "content": user if isinstance(user, list) else user}],
        )
    )
    latency_ms = int((time.perf_counter() - t0) * 1000)
    telemetry.log_call(agent=tool_ad, model=model, usage=resp.usage, latency_ms=latency_ms)

    for blok in resp.content:
        if blok.type == "tool_use" and blok.name == tool_ad:
            return schema.model_validate(blok.input)
    raise RuntimeError(f"Model '{tool_ad}' aracini cagirmadi. Yanit: {resp.content!r}")
