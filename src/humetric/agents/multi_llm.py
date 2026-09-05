"""Multi-provider LLM adapter — routes structured calls to Anthropic, OpenAI, Google AI, or DeepSeek."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from .. import config

_log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

SUPPORTED_PROVIDERS = ("anthropic", "openai", "google", "deepseek")


class LLMOutputParseError(RuntimeError):
    """The provider returned content that could not be parsed into the schema.

    Deliberately a RuntimeError and not a ValueError: worker.handle_failure
    treats this as retryable, and a ValueError base would put it back in the
    permanently-failed bucket that this class exists to get signals out of.
    """

    def __init__(
        self,
        message: str,
        *,
        raw_output: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        attempts: int = 1,
    ) -> None:
        self.raw_output = raw_output
        self.provider = provider
        self.model = model
        self.attempts = attempts
        prefix = f"llm_output_format: provider={provider} model={model} attempts={attempts}"
        parts = [f"{prefix} {message}"]
        if raw_output:
            parts.append(f"raw_output={_truncate_raw(raw_output)!r}")
        super().__init__(" ".join(parts))


def _truncate_raw(text: str, limit: int | None = None) -> str:
    """Cap raw provider output, marking the cut so nobody reads it as complete."""
    limit = config.LLM_ERROR_RAW_MAX_CHARS if limit is None else limit
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... [truncated, {len(text)} chars total]"


def _resolve_refs(node: object, defs: dict) -> object:
    """Inline every $ref against $defs so the model never has to dereference.

    Models routinely fail to follow "$ref": "#/$defs/X" and invent a flat shape
    instead. Recursion is bounded by the schema being a finite tree; Pydantic
    does not emit self-referential schemas for the models used here.
    """
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            target = defs.get(ref.split("/")[-1], {})
            return _resolve_refs(target, defs)
        return {k: _resolve_refs(v, defs) for k, v in node.items() if k != "$defs"}
    if isinstance(node, list):
        return [_resolve_refs(item, defs) for item in node]
    return node


def _json_example(node: object) -> object:
    """Build a minimal literal instance of a (already ref-resolved) schema.

    DeepSeek's JSON mode documentation explicitly asks for a format example in
    the prompt; a bare schema is not enough to pin the shape down.
    """
    if not isinstance(node, dict):
        return None
    node_type = node.get("type")
    if node_type == "object":
        props: dict = node.get("properties", {}) or {}
        required = node.get("required") or list(props)
        return {key: _json_example(props[key]) for key in required if key in props}
    if node_type == "array":
        return [_json_example(node.get("items", {}))]
    if node_type == "string":
        return "..."
    if node_type == "number":
        return 0.5
    if node_type == "integer":
        return 1
    if node_type == "boolean":
        return False
    # anyOf (e.g. `str | None`) — use the first non-null branch.
    for branch in node.get("anyOf", []) or []:
        if isinstance(branch, dict) and branch.get("type") != "null":
            return _json_example(branch)
    return None


def _schema_injection(system: str, schema: Type[T]) -> str:
    """Append JSON schema instructions to the system prompt for non-Anthropic providers."""
    raw = schema.model_json_schema()
    resolved = _resolve_refs(raw, raw.get("$defs", {}))
    schema_json = json.dumps(resolved, indent=2)
    example_json = json.dumps(_json_example(resolved), ensure_ascii=False)
    return (
        system
        + "\n\nRespond ONLY with a valid json object matching this exact schema "
        "(no markdown, no code fences, no extra text):\n"
        + schema_json
        + "\n\nExample of the exact expected shape:\n"
        + example_json
        + "\n\nEvery array must contain items of the declared type. When the "
        "declared item type is an object, emit a list of objects — never a bare "
        "name/value pair in place of an object, and never a name used as a JSON key."
    )


def _strip_code_fence(text: str) -> str:
    """Drop a leading ```json / ``` fence and its closing counterpart."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    if stripped.rstrip().endswith("```"):
        stripped = stripped.rstrip()[: -len("```")]
    return stripped


def _slice_outermost_object(text: str) -> str | None:
    """Return the span from the first '{' to the last '}', if both exist."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1]


def _coerce_to_schema(content: str | None, schema: Type[T]) -> tuple[T | None, str]:
    """Parse provider content into `schema`, salvaging common wrapper noise.

    Returns (parsed, "") on success and (None, error) on failure — the caller
    decides whether to re-ask, so this never raises.

    Only presentation-level damage is repaired: markdown fences and prose
    wrapped around the object. Structural repair (balancing quotes, dropping
    trailing commas, reshaping a bare "key": value pair inside an array into an
    object) is deliberately NOT attempted. That last one is the tempting fix for
    the DeepSeek failure this function exists for, and it is the wrong one:
    nothing tells us what confidence a reshaped pair should carry, so repairing
    it would invent a metric value. A metric engine that fails a signal is much
    better than one that silently fabricates a score.
    """
    if content is None or not content.strip():
        return None, "provider returned empty content"

    candidates = [content]
    unfenced = _strip_code_fence(content)
    if unfenced != content:
        candidates.append(unfenced)
    sliced = _slice_outermost_object(unfenced)
    if sliced and sliced not in candidates:
        candidates.append(sliced)

    first_error = ""
    for candidate in candidates:
        try:
            return schema.model_validate_json(candidate), ""
        except ValidationError as exc:
            if not first_error:
                first_error = str(exc).replace("\n", " ")
    return None, first_error


def _repair_user_message(parse_error: str, schema: Type[T]) -> str:
    """The correction turn sent back to the model after a parse failure."""
    raw = schema.model_json_schema()
    example = json.dumps(_json_example(_resolve_refs(raw, raw.get("$defs", {}))), ensure_ascii=False)
    return (
        "Your previous reply could not be parsed as json matching the required "
        f"schema. The parser reported: {parse_error}\n\n"
        "Reply again with ONE corrected json object and nothing else — no "
        "markdown fences, no prose before or after. Every array must contain "
        "items of the declared type: when the declared item type is an object, "
        "emit complete objects, never a bare name/value pair in place of one.\n\n"
        "Expected shape:\n" + example
    )


async def _parse_or_reask(
    *,
    schema: Type[T],
    raw: str | None,
    provider: str,
    model: str,
    reask: Callable[[str | None, str], Awaitable[str | None]],
    call_meta: dict | None = None,
) -> T:
    """Parse `raw`, re-asking the provider with the parse error fed back.

    Re-issuing the identical request is useless here: the happy path pins
    temperature to 0, so the provider deterministically reproduces the same
    bad bytes. Recovery depends on changing the conversation — the bad reply
    and the parser's own complaint are appended as extra turns, which is what
    actually lets the model diverge.
    """
    parsed, error = _coerce_to_schema(raw, schema)
    attempts = 1
    while parsed is None and attempts <= config.LLM_FORMAT_RETRIES:
        _log.warning(
            "Malformed %s output from provider=%s model=%s (attempt %d): %s — re-asking",
            schema.__name__, provider, model, attempts, error,
        )
        raw = await reask(raw, error)
        attempts += 1
        parsed, error = _coerce_to_schema(raw, schema)

    if parsed is None:
        raise LLMOutputParseError(
            f"could not parse output into {schema.__name__}: {error}",
            raw_output=raw,
            provider=provider,
            model=model,
            attempts=attempts,
        )

    if call_meta is not None:
        call_meta["format_repairs"] = attempts - 1
    return parsed


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
    call_meta: dict | None = None,
    signal_id: str | None = None,
    pack_key: str | None = None,
    pack_version: int | None = None,
    provider: str | None = None,
) -> T:
    client = _get_openai_client(api_key, base_url)
    user_text = user if isinstance(user, str) else json.dumps(user, ensure_ascii=False)
    enhanced_system = _schema_injection(system, schema)

    base_messages: list[dict] = [
        {"role": "system", "content": enhanced_system},
        {"role": "user", "content": user_text},
    ]

    async def _complete(messages: list[dict], temperature: float) -> str | None:
        t0 = time.perf_counter()
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=temperature,
            max_tokens=config.MAX_TOKENS,
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)

        usage = resp.usage
        total_tokens = (usage.prompt_tokens if usage else 0) + (
            usage.completion_tokens if usage else 0
        )
        _log.debug("OpenAI call model=%s tokens=%d latency=%dms", model, total_tokens, latency_ms)

        # A re-ask is a real billable call, so meter it like any other.
        if tenant_id is not None and total_tokens > 0:
            try:
                from ..services.usage_service import record_llm_tokens
                await record_llm_tokens(
                    tenant_id, total_tokens,
                    signal_id=signal_id, pack_key=pack_key, pack_version=pack_version,
                    provider=provider, model=model,
                )
            except Exception:
                _log.exception("Failed to record LLM tokens for tenant %d", tenant_id)

        return resp.choices[0].message.content

    async def _reask(previous: str | None, parse_error: str) -> str | None:
        return await _complete(
            base_messages
            + [
                {"role": "assistant", "content": _truncate_raw(
                    previous or "", config.LLM_REPAIR_ECHO_MAX_CHARS,
                )},
                {"role": "user", "content": _repair_user_message(parse_error, schema)},
            ],
            config.LLM_REPAIR_TEMPERATURE,
        )

    content = await _complete(base_messages, 0)

    if call_meta is not None:
        from .versioning import hash_prompt, hash_schema
        call_meta["model"] = model
        call_meta["prompt_hash"] = hash_prompt(system)
        call_meta["schema_hash"] = hash_schema(schema)

    return await _parse_or_reask(
        schema=schema,
        raw=content,
        provider=provider or "openai",
        model=model,
        reask=_reask,
        call_meta=call_meta,
    )


# ── Google AI (Gemini) ─────────────────────────────────────────────────────

# genai.configure() mutates global SDK state, so without a lock concurrent
# requests could leak one tenant's API key into another's call. _google_lock
# serializes configure→generate as a correctness guarantee; it also serializes
# all Google calls, so before opening this provider to heavy traffic migrate
# to the client-based `google-genai` SDK (genai.Client(api_key=...)) where
# each call carries its own credential.
_google_lock = asyncio.Lock()


def _google_response_text(resp: object) -> str | None:
    """Extract text from a Gemini response without letting the SDK raise.

    `resp.text` is a convenience property that throws when the candidate was
    blocked or finished abnormally. That exception is a ValueError, so before
    this guard it reached worker.handle_failure and was classified permanent —
    the same bug as the malformed-JSON one, in a second disguise. Returning
    None instead routes it through the normal parse-failure path.
    """
    feedback = getattr(resp, "prompt_feedback", None)
    if feedback is not None and getattr(feedback, "block_reason", None):
        _log.warning("Google AI blocked the prompt: %s", feedback.block_reason)
        return None
    candidates = getattr(resp, "candidates", None) or []
    if not candidates:
        return None
    parts = getattr(getattr(candidates[0], "content", None), "parts", None) or []
    text = "".join(getattr(part, "text", "") or "" for part in parts)
    return text or None


async def _call_google(
    *,
    model: str,
    api_key: str,
    system: str,
    user: str | list,
    schema: Type[T],
    tenant_id: int | None = None,
    call_meta: dict | None = None,
    signal_id: str | None = None,
    pack_key: str | None = None,
    pack_version: int | None = None,
    provider: str | None = None,
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

    async def _generate(contents: list[dict] | str, temperature: float) -> str | None:
        t0 = time.perf_counter()
        async with _google_lock:
            genai.configure(api_key=api_key)
            model_obj = genai.GenerativeModel(
                model_name=model,
                system_instruction=enhanced_system,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=temperature,
                    max_output_tokens=config.MAX_TOKENS,
                ),
            )
            resp = await asyncio.to_thread(lambda: model_obj.generate_content(contents))
        latency_ms = int((time.perf_counter() - t0) * 1000)

        total_tokens = 0
        if hasattr(resp, "usage_metadata") and resp.usage_metadata:
            m = resp.usage_metadata
            total_tokens = (getattr(m, "prompt_token_count", 0) or 0) + (
                getattr(m, "candidates_token_count", 0) or 0
            )
        _log.debug(
            "Google AI call model=%s tokens=%d latency=%dms", model, total_tokens, latency_ms,
        )

        if tenant_id is not None and total_tokens > 0:
            try:
                from ..services.usage_service import record_llm_tokens
                await record_llm_tokens(
                    tenant_id, total_tokens,
                    signal_id=signal_id, pack_key=pack_key, pack_version=pack_version,
                    provider=provider, model=model,
                )
            except Exception:
                _log.exception("Failed to record LLM tokens for tenant %d", tenant_id)

        return _google_response_text(resp)

    async def _reask(previous: str | None, parse_error: str) -> str | None:
        return await _generate(
            [
                {"role": "user", "parts": [user_text]},
                {"role": "model", "parts": [_truncate_raw(
                    previous or "", config.LLM_REPAIR_ECHO_MAX_CHARS,
                )]},
                {"role": "user", "parts": [_repair_user_message(parse_error, schema)]},
            ],
            config.LLM_REPAIR_TEMPERATURE,
        )

    content = await _generate(user_text, 0)

    if call_meta is not None:
        from .versioning import hash_prompt, hash_schema
        call_meta["prompt_hash"] = hash_prompt(system)
        call_meta["schema_hash"] = hash_schema(schema)
        call_meta["model"] = model

    return await _parse_or_reask(
        schema=schema,
        raw=content,
        provider=provider or "google",
        model=model,
        reask=_reask,
        call_meta=call_meta,
    )


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
    signal_id: str | None = None,
    pack_key: str | None = None,
    pack_version: int | None = None,
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
            signal_id=signal_id,
            pack_key=pack_key,
            pack_version=pack_version,
            provider=provider,
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
            call_meta=call_meta,
            signal_id=signal_id,
            pack_key=pack_key,
            pack_version=pack_version,
            provider=provider,
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
            call_meta=call_meta,
            signal_id=signal_id,
            pack_key=pack_key,
            pack_version=pack_version,
            provider=provider,
        )

    if provider == "google":
        return await _call_google(
            model=model,
            api_key=api_key,
            system=system,
            user=user,
            schema=schema,
            tenant_id=tenant_id,
            call_meta=call_meta,
            signal_id=signal_id,
            pack_key=pack_key,
            pack_version=pack_version,
            provider=provider,
        )

    raise ValueError(
        f"Unsupported LLM provider: '{provider}'. "
        f"Valid choices: {', '.join(SUPPORTED_PROVIDERS)}"
    )
