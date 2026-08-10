"""Multi-provider LLM adapter — routes structured calls to Anthropic, OpenAI, Google AI, or DeepSeek."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable, Type, TypeVar

from pydantic import BaseModel

from .. import config
from .jury import JuryVote, deliberate

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

# Each call builds its own client from the caller's key (genai.Client(api_key=…)),
# so nothing about a tenant's credential is global — concurrent calls from
# different tenants can no longer leak keys into one another, and Google calls
# are not serialized behind a process-wide lock.
_google_clients: dict[str, object] = {}


def _get_google_client(api_key: str):
    if api_key not in _google_clients:
        try:
            from google import genai
        except ImportError:
            raise RuntimeError(
                "The 'google-genai' package is required for the Google AI provider. "
                "Install it with: pip install google-genai>=1.0"
            )
        _google_clients[api_key] = genai.Client(api_key=api_key)
    return _google_clients[api_key]


async def _call_google(
    *,
    model: str,
    api_key: str,
    system: str,
    user: str | list,
    schema: Type[T],
    tenant_id: int | None = None,
) -> T:
    client = _get_google_client(api_key)

    user_text = user if isinstance(user, str) else json.dumps(user, ensure_ascii=False)
    enhanced_system = _schema_injection(system, schema)

    t0 = time.perf_counter()
    resp = await client.aio.models.generate_content(
        model=model,
        contents=user_text,
        config={
            "system_instruction": enhanced_system,
            "response_mime_type": "application/json",
            "temperature": 0,
            "max_output_tokens": config.MAX_TOKENS,
        },
    )
    latency_ms = int((time.perf_counter() - t0) * 1000)

    total_tokens = 0
    usage = getattr(resp, "usage_metadata", None)
    if usage:
        total_tokens = (getattr(usage, "prompt_token_count", 0) or 0) + (
            getattr(usage, "candidates_token_count", 0) or 0
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

    # The Anthropic path fills call_meta itself; do the same here so a trace
    # is equally auditable whichever provider produced the metric.
    def _fill_meta() -> None:
        if call_meta is None:
            return
        from .versioning import hash_prompt, hash_schema
        call_meta["prompt_hash"] = hash_prompt(system)
        call_meta["schema_hash"] = hash_schema(schema)
        call_meta["model"] = model

    if provider == "openai":
        result = await _call_openai(
            model=model,
            api_key=api_key,
            system=system,
            user=user,
            schema=schema,
            tenant_id=tenant_id,
        )
        _fill_meta()
        return result

    if provider == "deepseek":
        result = await _call_openai(
            model=model,
            api_key=api_key,
            system=system,
            user=user,
            schema=schema,
            base_url="https://api.deepseek.com/v1",
            tenant_id=tenant_id,
        )
        _fill_meta()
        return result

    if provider == "google":
        result = await _call_google(
            model=model,
            api_key=api_key,
            system=system,
            user=user,
            schema=schema,
            tenant_id=tenant_id,
        )
        _fill_meta()
        return result

    raise ValueError(
        f"Unsupported LLM provider: '{provider}'. "
        f"Valid choices: {', '.join(SUPPORTED_PROVIDERS)}"
    )


# ── Jury dispatcher ────────────────────────────────────────────────────────

async def structured_call_jury(
    *,
    members: list[tuple[str, str | None]],
    system: str,
    user: str | list,
    schema: Type[T],
    tool_name: str,
    tool_description: str,
    model_for: Callable[[str], str],
    jury_strategy: str = "best_of",
    min_agreement: float = 0.0,
    tenant_id: int | None = None,
    call_meta: dict | None = None,
) -> T:
    """Run the request on every member in parallel and reconcile the answers.

    With a single member this is exactly `structured_call_multi` — no extra
    latency, no jury bookkeeping — so callers can use one code path regardless
    of how many providers the tenant activated.

    Members that fail are tolerated as long as one succeeds: a rate-limited
    provider must not take down a signal that the others answered fine. If all
    of them fail, the first error is raised so the task retries as before.

    `model_for(provider)` maps a provider to its model, so the caller keeps
    control of which agent tier runs (extractor/curator/ranker).
    """
    if not members:
        raise RuntimeError("structured_call_jury requires at least one member")

    if len(members) == 1:
        provider, api_key = members[0]
        result = await structured_call_multi(
            provider=provider,
            model=model_for(provider),
            api_key=api_key,
            system=system,
            user=user,
            schema=schema,
            tool_name=tool_name,
            tool_description=tool_description,
            tenant_id=tenant_id,
            call_meta=call_meta,
        )
        if call_meta is not None:
            # Recorded on both paths so a trace always names the provider,
            # whether or not a jury ran.
            call_meta.setdefault("provider", provider)
        return result

    async def _run(provider: str, api_key: str | None) -> JuryVote:
        model = model_for(provider)
        started = time.perf_counter()
        # Each member gets its own meta dict; only the winner's is promoted to
        # the caller's call_meta, so the trace names the model that decided.
        member_meta: dict = {}
        try:
            value = await structured_call_multi(
                provider=provider,
                model=model,
                api_key=api_key,
                system=system,
                user=user,
                schema=schema,
                tool_name=tool_name,
                tool_description=tool_description,
                tenant_id=tenant_id,
                call_meta=member_meta,
            )
            vote = JuryVote(
                provider=provider,
                model=model,
                ok=True,
                latency_ms=int((time.perf_counter() - started) * 1000),
                value=value,
            )
        except Exception as exc:
            _log.warning("Jury member %s (%s) failed: %s", provider, model, exc)
            return JuryVote(
                provider=provider,
                model=model,
                ok=False,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error=f"{type(exc).__name__}: {exc}",
            )
        vote.meta = member_meta
        return vote

    votes: list[JuryVote] = list(
        await asyncio.gather(*(_run(p, k) for p, k in members))
    )

    successful = [v for v in votes if v.ok]
    if not successful:
        detail = "; ".join(f"{v.provider}: {v.error}" for v in votes)
        raise RuntimeError(f"All jury members failed — {detail}")

    decision, report = deliberate(
        votes, schema, strategy=jury_strategy, min_agreement=min_agreement,
    )

    if call_meta is not None:
        winner = report.decisive_provider
        winning_vote = next(
            (v for v in successful if v.provider == winner), successful[0]
        )
        call_meta.update(winning_vote.meta or {})
        call_meta["model"] = winning_vote.model
        call_meta["provider"] = winning_vote.provider
        # The jury summary is what makes a multi-provider decision auditable:
        # who took part, how far they agreed, which fields they diverged on.
        call_meta["jury"] = report.summary()

    _log.info(
        "Jury decided via %s: winner=%s agreement=%.2f members=%d ok=%d",
        report.strategy, report.decisive_provider, report.agreement,
        len(votes), len(successful),
    )
    return decision  # type: ignore[return-value]
