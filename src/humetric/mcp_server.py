#!/usr/bin/env python3
"""HuMetric MCP Server — Claude integration (Spec 026).

Exposes the entirety of the HuMetric REST API as tools Claude can use.
Transport: stdio (Claude Desktop) or SSE / streamable-http (remote host).

Usage:
  humetric-mcp                                  # stdio (after pip install -e .)
  humetric-mcp --transport sse --port 8765
  python -m humetric.mcp_server                 # development, no install needed

Configuration (.env or environment variable):
  HUMETRIC_MCP_API_KEY   required — hm_live_... API key
  HUMETRIC_BASE_URL      default http://localhost:8002
  HUMETRIC_MCP_TIMEOUT_S default 30

--- Scope decision ------------------------------------------------------------
The API has 41 endpoints; 25 are exposed here as tools. The ones left out and
why (intentional, not an oversight):

  /v1/register, /v1/login, /v1/verify-email
      Account creation. An MCP session already runs with an authenticated
      key; opening a new account from here is pointless and risky.
  /v1/api-keys (POST/DELETE), /v1/tenant/rotate-api-key
      Credential creation/revocation. Letting an agent manage its own access
      keys defeats the point of scoping its permissions.
  /v1/tenant/keys (GET/PUT/DELETE)
      BYOK provider secrets. Neither reading nor writing these is an agent's job.
  /v1/billing/checkout, /v1/billing/webhook
      Money movement and the Stripe callback.
  /v1/admin/usage
      Admin reporting beyond tenant boundaries.
  /v1/packs/wizard
      The endpoint that has an LLM write the YAML. The LLM here is already
      Claude — it's both cheaper and better for it to write the pack itself
      and publish it with humetric_create_pack.
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import json
import logging
import os
import sys
import uuid
from typing import Annotated, Any

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP as MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

load_dotenv()

# On the stdio transport, stdout is ONLY for protocol messages; a single
# stray line breaks the connection. All logs go to stderr.
logging.basicConfig(
    stream=sys.stderr,
    level=os.environ.get("HUMETRIC_MCP_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
_log = logging.getLogger("humetric.mcp")

# Not imported from the package (this module is intentionally standalone —
# see the file header); api.py likewise keeps its own __version__.
__version__ = "1.0.0"

BASE_URL = os.environ.get("HUMETRIC_BASE_URL", "http://localhost:8002").rstrip("/")
TIMEOUT_S = float(os.environ.get("HUMETRIC_MCP_TIMEOUT_S", "30"))
API_PREFIX = "/v1"

# Large lists shouldn't needlessly fill Claude's context; the truncated
# amount is stated explicitly in the response so it isn't mistaken for the
# full data set.
MAX_ITEMS = int(os.environ.get("HUMETRIC_MCP_MAX_ITEMS", "50"))


def _api_key() -> str:
    """Read the key at call time.

    Deliberately not checked at module load: the previous version called
    sys.exit(1) here, and Claude Desktop only ever showed "server failed to
    start" for that — the actual reason never reached the user. This way the
    server starts up and explains the missing configuration in plain
    language on the first tool call.
    """
    return os.environ.get("HUMETRIC_MCP_API_KEY", "").strip()


_client: httpx.AsyncClient | None = None


async def _get_client() -> httpx.AsyncClient:
    """A single AsyncClient is shared — opening a new connection pool on
    every call is both slow and needlessly repeats the TLS handshake."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=httpx.Timeout(TIMEOUT_S),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )
    return _client


class ApiError(Exception):
    """An error returned by the API, safe to show to the user."""


async def _request(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    authenticated: bool = True,
) -> Any:
    key = _api_key()
    if authenticated and not key:
        raise ApiError(
            "HUMETRIC_MCP_API_KEY is not set. Add your HuMetric API key "
            "(hm_live_...) to the env block in the Claude Desktop configuration "
            "and restart the app."
        )

    # These headers flow into the API's usage_record and make "which key
    # called which tool" answerable. Descriptive data only: the tenant and
    # key identity are resolved from Authorization, not from these.
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"humetric-mcp/{__version__}",
        "X-HuMetric-Client": "mcp",
    }
    if tool := _current_tool.get():
        headers["X-HuMetric-Tool"] = tool
    if call_id := _current_call_id.get():
        headers["X-HuMetric-Call-Id"] = call_id
    if authenticated:
        headers["Authorization"] = f"Bearer {key}"

    resp = await _send_with_retry(method, path, json_body, params, headers)

    if resp.status_code == 204 or not resp.content:
        return {"ok": True, "status": resp.status_code}

    try:
        data = resp.json()
    except ValueError:
        raise ApiError(f"API returned a non-JSON response (HTTP {resp.status_code}).")

    if resp.is_success:
        return data

    raise ApiError(_describe_error(resp.status_code, data))


async def _send_with_retry(
    method: str,
    path: str,
    json_body: dict[str, Any] | None,
    params: dict[str, Any] | None,
    headers: dict[str, str],
) -> httpx.Response:
    """Send the request; retry once on a transport-layer error.

    Why this is needed: when the API returns a 5xx it can close the
    connection, but the keep-alive entry in the pool is still assumed alive.
    The next request reuses that dead connection and gets an httpx.ReadError
    — whose message is EMPTY, so the user used to get a blank "error
    occurred: " line. Refreshing the pool and retrying once is the right
    behavior; if it fails a second time, there's a real problem.
    """
    last_exc: Exception | None = None
    for attempt in (1, 2):
        client = await _get_client()
        try:
            return await client.request(
                method, path, json=json_body, params=_clean_params(params), headers=headers
            )
        except httpx.TimeoutException as exc:
            raise ApiError(
                f"HuMetric API did not respond within {TIMEOUT_S:.0f} seconds ({path})."
            ) from exc
        except httpx.TransportError as exc:
            last_exc = exc
            _log.warning(
                "Connection error (%s), attempt %d/2: %s", type(exc).__name__, attempt, exc or "(empty message)"
            )
            await _reset_client()

    raise ApiError(
        f"Could not reach the HuMetric API ({BASE_URL}{path}): "
        f"{type(last_exc).__name__}. Is the service running, is HUMETRIC_BASE_URL correct?"
    ) from last_exc


async def _reset_client() -> None:
    """Close the connection pool; the next call opens a fresh client."""
    global _client
    if _client is not None and not _client.is_closed:
        try:
            await _client.aclose()
        except Exception:  # noqa: BLE001 - a close error shouldn't hide the real issue
            pass
    _client = None


def _clean_params(params: dict[str, Any] | None) -> dict[str, Any] | None:
    """Drop None values — httpx would otherwise send them as the string "None"."""
    if not params:
        return None
    cleaned = {k: v for k, v in params.items() if v is not None}
    return cleaned or None


def _describe_error(status: int, data: Any) -> str:
    """Turn HuMetric's error envelope ({"error": {...}}) into a readable sentence."""
    envelope = data.get("error") if isinstance(data, dict) else None
    if isinstance(envelope, dict):
        code = envelope.get("code", "error")
        message = envelope.get("message", "")
        doc = envelope.get("doc_url")
        text = f"[{status} {code}] {message}"
        return f"{text} — {doc}" if doc else text

    # FastAPI validation error: which field was rejected and why, right here.
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, list) and detail:
        parts = []
        for item in detail[:5]:
            loc = ".".join(str(x) for x in item.get("loc", []) if x != "body")
            parts.append(f"{loc}: {item.get('msg', '')}")
        return f"[{status} validation_error] " + "; ".join(parts)
    if detail:
        return f"[{status}] {detail}"
    return f"[{status}] {json.dumps(data, ensure_ascii=False)[:400]}"


def _render(data: Any, *, max_items: int = MAX_ITEMS) -> str:
    """Convert the response into compact JSON for Claude, truncating long lists.

    Truncation is never silent: the number of hidden items is written into
    the response, otherwise Claude might mistake it for the complete list
    and draw the wrong conclusion.
    """
    data = _truncate(data, max_items)
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _truncate(data: Any, max_items: int) -> Any:
    if isinstance(data, list):
        if len(data) > max_items:
            return data[:max_items] + [
                {"_truncated": f"{len(data) - max_items} more item(s); "
                              f"paginate with limit/offset"}
            ]
        return data
    if isinstance(data, dict):
        return {k: _truncate(v, max_items) for k, v in data.items()}
    return data


READ_ONLY = ToolAnnotations(readOnlyHint=True)
WRITES = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)


# ── Call identity ───────────────────────────────────────────────────────────
#
# The API side couldn't distinguish a request coming from MCP from one
# coming from curl; these two contextvars and the subclass below fix that.
# No need to touch all 25 tool functions to capture the tool name:
# FastMCP.call_tool is the single pass-through point for every call, so
# overriding it is enough.

_current_tool: contextvars.ContextVar[str] = contextvars.ContextVar(
    "humetric_mcp_tool", default=""
)
_current_call_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "humetric_mcp_call_id", default=""
)


class InstrumentedMCP(MCPServer):
    """Names every tool call and tags it with a single call_id.

    Why call_id is needed: a single tool call can trigger multiple HTTP
    requests — humetric_health fires three separate /healthz* requests.
    Counting requests would make the user's one call look like three;
    the shared call_id groups them back into a single call.
    """

    async def call_tool(self, name: str, arguments: dict[str, Any]):
        tool_token = _current_tool.set(name)
        call_token = _current_call_id.set(uuid.uuid4().hex)
        try:
            return await super().call_tool(name, arguments)
        finally:
            _current_tool.reset(tool_token)
            _current_call_id.reset(call_token)


server = InstrumentedMCP(
    name="humetric",
    instructions="""
HuMetric: an agentic metric engine that produces entity metrics from signals.

HuMetric is a service that extracts metrics about entities from free-text or
structured "signals". Typical flow:

  1. A Metric Pack defines which metrics are measured for which entity type.
  2. A signal about an entity is submitted (humetric_ingest_signal).
  3. The signal is processed ASYNCHRONOUSLY: the response status is usually
     "queued". Don't query the metrics right away — poll with
     humetric_get_signal until the status is "completed", then read the
     metrics.
  4. Metrics are read (humetric_get_entity_metrics) or entities are ranked
     by a metric (humetric_query_entities).

Operating rules:

* Discover first: if you don't know the entity type or metric key, check
  the available packs with humetric_list_packs. Don't guess metric keys —
  a metric not defined in the pack is never produced.
* If you need to justify a metric's value, use humetric_explain_metric; it
  returns which signals contributed to that value. Use
  humetric_metric_history for change over time.
* Don't present low-confidence metrics as certain fact; always convey the
  confidence alongside the value.
* KVKK (data protection): metrics marked as sensitive are only processed
  when explicit consent exists for the entity in question. Before sending
  a signal that would produce sensitive data, check the status with
  humetric_get_consent; if there's no consent, tell the user — don't call
  humetric_grant_consent yourself. Consent is the data subject's
  declaration, not your assumption.
* Write operations (submitting a signal, publishing a pack, overriding a
  metric) change real data. Don't do this without the user's explicit
  request.
* When a tool returns an error, relay the message as-is; error codes
  (e.g. tier_limit_exceeded, consent_required) are meaningful to the user.
""".strip(),
)


# ── Signals ──────────────────────────────────────────────────────────────────

@server.tool(
    description=(
        "Submits a signal about an entity; metric extraction starts asynchronously. "
        "Track the status with humetric_get_signal using the signal_id from the "
        "response — metrics aren't up to date until status is 'completed'."
    ),
    annotations=WRITES,
)
async def humetric_ingest_signal(
    entity_id: Annotated[str, Field(description="Entity identifier. If it doesn't exist, the entity is created automatically.")],
    entity_type: Annotated[str, Field(description="Entity type; must be defined in a Metric Pack (e.g. 'support_agent').")],
    text: Annotated[str | None, Field(description="Free-text signal: a review, note, transcript, or feedback.")] = None,
    structured: Annotated[dict[str, Any] | None, Field(description="Structured signal fields (numeric/categorical data).")] = None,
    external_id: Annotated[str | None, Field(description="Identifier in the source system. Resubmitting the same id does not duplicate the signal.")] = None,
    occurred_at: Annotated[str | None, Field(description="When the event actually happened (ISO-8601). For backdated ingestion; defaults to 'now' if left blank.")] = None,
) -> str:
    if not text and not structured:
        raise ApiError("At least one of text or structured must be provided — an empty signal produces no metrics.")
    body = {"entity_id": entity_id, "entity_type": entity_type}
    for key, value in (("text", text), ("structured", structured),
                       ("external_id", external_id), ("occurred_at", occurred_at)):
        if value is not None:
            body[key] = value
    return _render(await _request("POST", f"{API_PREFIX}/signals", json_body=body))


@server.tool(
    description="Returns a signal's processing status (queued / processing / completed / failed).",
    annotations=READ_ONLY,
)
async def humetric_get_signal(
    signal_id: Annotated[str, Field(description="The signal_id returned by humetric_ingest_signal.")],
) -> str:
    return _render(await _request("GET", f"{API_PREFIX}/signals/{signal_id}"))


@server.tool(
    description=(
        "Returns a signal's processing trace: what each agent extracted, what "
        "the curator changed and how. Use this when discussing why a metric ended up at that value."
    ),
    annotations=READ_ONLY,
)
async def humetric_get_signal_trace(
    signal_id: Annotated[str, Field(description="Identifier of the signal to trace.")],
) -> str:
    return _render(await _request("GET", f"{API_PREFIX}/signals/{signal_id}/trace"))


@server.tool(
    description="Lists the signals for an entity (newest first).",
    annotations=READ_ONLY,
)
async def humetric_list_entity_signals(
    entity_id: Annotated[str, Field(description="Entity identifier.")],
    status: Annotated[str | None, Field(description="Filter by status: queued, processing, completed, failed.")] = None,
    limit: Annotated[int, Field(description="How many records to return (1-200).", ge=1, le=200)] = 20,
    offset: Annotated[int, Field(description="Number of records to skip for pagination.", ge=0)] = 0,
) -> str:
    return _render(await _request(
        "GET", f"{API_PREFIX}/entities/{entity_id}/signals",
        params={"status": status, "limit": limit, "offset": offset},
    ))


# ── Entities and metrics ──────────────────────────────────────────────────────

@server.tool(
    description=(
        "Creates or updates an entity (upsert). Submitting a signal already "
        "creates the entity automatically; only use this when you want to "
        "pre-populate fields like name/segment ahead of time."
    ),
    annotations=WRITES,
)
async def humetric_upsert_entity(
    id: Annotated[str, Field(description="Entity identifier (the id in your own system).")],
    entity_type: Annotated[str, Field(description="Entity type; must be defined in a Metric Pack.")],
    fields: Annotated[dict[str, Any] | None, Field(description="Structured fields: name, department, segment, etc.")] = None,
    free_text: Annotated[str | None, Field(description="Free text describing the entity (used for search/matching).")] = None,
    status: Annotated[str, Field(description="Entity status: active or inactive.")] = "active",
) -> str:
    body: dict[str, Any] = {"id": id, "entity_type": entity_type, "status": status}
    if fields is not None:
        body["fields"] = fields
    if free_text is not None:
        body["free_text"] = free_text
    return _render(await _request("POST", f"{API_PREFIX}/entities", json_body=body))


@server.tool(
    description="Returns a single entity along with its fields.",
    annotations=READ_ONLY,
)
async def humetric_get_entity(
    entity_id: Annotated[str, Field(description="Entity identifier.")],
) -> str:
    return _render(await _request("GET", f"{API_PREFIX}/entities/{entity_id}"))


@server.tool(
    description="Lists entities. Use this to discover which entities are on record.",
    annotations=READ_ONLY,
)
async def humetric_list_entities(
    entity_type: Annotated[str | None, Field(description="Filter by type (e.g. 'driver').")] = None,
    limit: Annotated[int, Field(description="How many records to return (1-200).", ge=1, le=200)] = 50,
    offset: Annotated[int, Field(description="Number of records to skip for pagination.", ge=0)] = 0,
) -> str:
    return _render(await _request(
        "GET", f"{API_PREFIX}/entities",
        params={"entity_type": entity_type, "limit": limit, "offset": offset},
    ))


@server.tool(
    description=(
        "Returns all of an entity's metrics with their value and confidence score. "
        "Don't present low-confidence metrics as certainty."
    ),
    annotations=READ_ONLY,
)
async def humetric_get_entity_metrics(
    entity_id: Annotated[str, Field(description="Entity identifier.")],
) -> str:
    return _render(await _request("GET", f"{API_PREFIX}/entities/{entity_id}/metrics"))


@server.tool(
    description=(
        "Explains why a metric has the value it does: the signals that "
        "contributed to it, the model, and the rationale. The answer to "
        "'where does this score come from?'"
    ),
    annotations=READ_ONLY,
)
async def humetric_explain_metric(
    entity_id: Annotated[str, Field(description="Entity identifier.")],
    metric_key: Annotated[str, Field(description="Metric key (e.g. 'resolution_speed').")],
) -> str:
    return _render(await _request(
        "GET", f"{API_PREFIX}/entities/{entity_id}/metrics/{metric_key}/explain"
    ))


@server.tool(
    description=(
        "Returns how a metric has changed over time. Use this for trend "
        "questions ('is it improving or declining')."
    ),
    annotations=READ_ONLY,
)
async def humetric_metric_history(
    entity_id: Annotated[str, Field(description="Entity identifier.")],
    metric_key: Annotated[str, Field(description="Metric key.")],
    since: Annotated[str | None, Field(description="Start instant (ISO-8601).")] = None,
    until: Annotated[str | None, Field(description="End instant (ISO-8601).")] = None,
    limit: Annotated[int, Field(description="How many points to return (1-500).", ge=1, le=500)] = 100,
    offset: Annotated[int, Field(description="Number of points to skip for pagination.", ge=0)] = 0,
) -> str:
    return _render(await _request(
        "GET", f"{API_PREFIX}/entities/{entity_id}/metrics/{metric_key}/history",
        params={"since": since, "until": until, "limit": limit, "offset": offset},
    ))


# ── Querying ─────────────────────────────────────────────────────────────────

@server.tool(
    description=(
        "Ranks entities by a metric and/or searches with free text. "
        "The tool for questions like 'who are the top 5 X', 'find entities with "
        "such-and-such property'. If rank_by is given, ranking is by that metric; "
        "if free_text_query is given, it's by semantic similarity; both can be used together."
    ),
    annotations=READ_ONLY,
)
async def humetric_query_entities(
    entity_type: Annotated[str | None, Field(description="Entity type to search.")] = None,
    rank_by: Annotated[str | None, Field(description="Metric key to rank by.")] = None,
    free_text_query: Annotated[str | None, Field(description="Semantic search text (e.g. 'patient and detail-oriented').")] = None,
    filters: Annotated[dict[str, Any] | None, Field(description="Field-level filters (e.g. {'department': 'support'}).")] = None,
    top_k: Annotated[int, Field(description="How many results to return (1-100).", ge=1, le=100)] = 10,
    include_reasoning: Annotated[bool, Field(description="Whether to also return the ranking rationale for each result. Increases token cost.")] = False,
) -> str:
    body: dict[str, Any] = {"top_k": top_k, "include_reasoning": include_reasoning}
    for key, value in (("entity_type", entity_type), ("rank_by", rank_by),
                       ("free_text_query", free_text_query), ("filters", filters)):
        if value is not None:
            body[key] = value
    return _render(await _request("POST", f"{API_PREFIX}/query", json_body=body))


# ── Metric Packs ───────────────────────────────────────────────────────────────

@server.tool(
    description=(
        "Lists the defined Metric Packs. The FIRST place to look to learn "
        "which entity types and metric keys are available."
    ),
    annotations=READ_ONLY,
)
async def humetric_list_packs(
    is_active: Annotated[bool | None, Field(description="Only active (True) or inactive (False) packs. Blank: all.")] = None,
) -> str:
    return _render(await _request("GET", f"{API_PREFIX}/packs", params={"is_active": is_active}))


@server.tool(
    description="Returns the full definition of a Metric Pack (YAML and the metric list).",
    annotations=READ_ONLY,
)
async def humetric_get_pack(
    pack_key: Annotated[str, Field(description="Pack key (e.g. 'support_agent_v1').")],
) -> str:
    return _render(await _request("GET", f"{API_PREFIX}/packs/{pack_key}"))


@server.tool(
    description=(
        "Publishes a new Metric Pack. You write the YAML yourself: entity_type, label, "
        "version, metrics[] (key, label, type, prompt, sensitive, default_confidence), "
        "and prompts/kvkk sections are required. Use humetric_get_pack to model it on "
        "an existing pack first — the format must match exactly."
    ),
    annotations=WRITES,
)
async def humetric_create_pack(
    yaml_text: Annotated[str, Field(description="The pack definition, as YAML text.")],
    pack_key: Annotated[str | None, Field(description="Pack key. Derived from the YAML if not given.")] = None,
) -> str:
    body: dict[str, Any] = {"yaml_text": yaml_text}
    if pack_key:
        body["pack_key"] = pack_key
    return _render(await _request("POST", f"{API_PREFIX}/packs", json_body=body))


@server.tool(
    description=(
        "Updates an existing Metric Pack (creates a new version). Changing the "
        "metric definition affects future extractions; it does not retroactively "
        "change past metrics."
    ),
    annotations=WRITES,
)
async def humetric_update_pack(
    pack_key: Annotated[str, Field(description="Key of the pack to update.")],
    yaml_text: Annotated[str, Field(description="The pack's new full definition (YAML).")],
) -> str:
    return _render(await _request(
        "PUT", f"{API_PREFIX}/packs/{pack_key}", json_body={"yaml_text": yaml_text, "pack_key": pack_key}
    ))


# ── Human review (reviewer override) ──────────────────────────────────────────

@server.tool(
    description=(
        "Lists metrics awaiting human review — ones flagged because their "
        "confidence score fell below the threshold."
    ),
    annotations=READ_ONLY,
)
async def humetric_list_pending_review() -> str:
    return _render(await _request("GET", f"{API_PREFIX}/metrics/pending-review"))


@server.tool(
    description=(
        "Overrides a metric's value with a human decision. This permanently changes "
        "the model's inference and is written to the audit log — don't call this "
        "without the user's explicit instruction, and don't write your own guess as a 'correction'."
    ),
    annotations=DESTRUCTIVE,
)
async def humetric_review_metric(
    entity_id: Annotated[str, Field(description="Entity identifier.")],
    metric_key: Annotated[str, Field(description="Metric key.")],
    value: Annotated[float, Field(description="The new value set by the human reviewer.")],
    confidence: Annotated[float, Field(description="Confidence in this value (0-1).", ge=0, le=1)],
    comment: Annotated[str | None, Field(description="Rationale for the change; written to the audit log.")] = None,
) -> str:
    body: dict[str, Any] = {"value": value, "confidence": confidence}
    if comment:
        body["comment"] = comment
    return _render(await _request(
        "PUT", f"{API_PREFIX}/metrics/{entity_id}/{metric_key}/review", json_body=body
    ))


# ── Consent (KVKK) ───────────────────────────────────────────────────────────

@server.tool(
    description=(
        "Returns an entity's consent status. Check this before submitting a "
        "signal that would produce sensitive metrics."
    ),
    annotations=READ_ONLY,
)
async def humetric_get_consent(
    entity_id: Annotated[str, Field(description="Entity identifier.")],
) -> str:
    return _render(await _request("GET", f"{API_PREFIX}/consent/{entity_id}"))


@server.tool(
    description=(
        "Creates a consent record for an entity. Consent is the data subject's own "
        "declaration: only call this when the user explicitly tells you 'they've "
        "consented, record it'. Don't fabricate consent yourself just to run an operation."
    ),
    annotations=WRITES,
)
async def humetric_grant_consent(
    entity_id: Annotated[str, Field(description="Entity identifier.")],
    scope: Annotated[str, Field(description="Consent scope (e.g. 'sensitive_data_processing').")],
    expires_at: Annotated[str | None, Field(description="When the consent expires (ISO-8601). Blank: no expiry.")] = None,
) -> str:
    body: dict[str, Any] = {"entity_id": entity_id, "scope": scope, "status": "granted"}
    if expires_at:
        body["expires_at"] = expires_at
    return _render(await _request("POST", f"{API_PREFIX}/consent", json_body=body))


@server.tool(
    description="Revokes an entity's consent. No sensitive metrics are processed for it afterward.",
    annotations=DESTRUCTIVE,
)
async def humetric_revoke_consent(
    entity_id: Annotated[str, Field(description="Entity identifier.")],
) -> str:
    return _render(await _request("DELETE", f"{API_PREFIX}/consent/{entity_id}"))


# ── Account and audit ─────────────────────────────────────────────────────────

@server.tool(
    description="Account summary: tier, subscription status, this month's usage, and tier limits.",
    annotations=READ_ONLY,
)
async def humetric_dashboard() -> str:
    return _render(await _request("GET", f"{API_PREFIX}/tenant/dashboard"))


@server.tool(
    description="Daily usage report over a date range: signal, LLM token, and embedding counts.",
    annotations=READ_ONLY,
)
async def humetric_usage_report(
    start_date: Annotated[str, Field(description="Start date (YYYY-MM-DD).")],
    end_date: Annotated[str, Field(description="End date (YYYY-MM-DD).")],
) -> str:
    return _render(await _request(
        "GET", f"{API_PREFIX}/usage", params={"start_date": start_date, "end_date": end_date}
    ))


@server.tool(
    description=(
        "Per-pack usage: entity/signal counts and LLM token spend broken down by "
        "Metric Pack. humetric_usage_report gives the daily tenant-wide total; "
        "this answers 'which pack cost how much'. Rows with kind='system' are "
        "spend that belongs to no pack (query re-ranking, pack wizard) and carry "
        "no entity/signal counts — they are included so the token totals "
        "reconcile with humetric_usage_report."
    ),
    annotations=READ_ONLY,
)
async def humetric_pack_usage(
    start_date: Annotated[str, Field(description="Start date (YYYY-MM-DD).")],
    end_date: Annotated[str, Field(description="End date (YYYY-MM-DD).")],
) -> str:
    return _render(await _request(
        "GET", f"{API_PREFIX}/usage/packs",
        params={"start_date": start_date, "end_date": end_date},
    ))


@server.tool(
    description=(
        "Call history: which API key called which MCP tool how many times, "
        "how long it took, how many errored. humetric_usage_report gives daily "
        "volume; this tool gives a per-request breakdown. "
        "tool_calls counts a tool call, http_requests counts the underlying "
        "requests fired — the difference between the two comes from tools that make many requests."
    ),
    annotations=READ_ONLY,
)
async def humetric_call_history(
    start_date: Annotated[str, Field(description="Start date (YYYY-MM-DD).")],
    end_date: Annotated[str, Field(description="End date (YYYY-MM-DD).")],
    group_by: Annotated[
        str,
        Field(description="Grouping axis: tool | key | client | endpoint | day | none."),
    ] = "tool",
    client: Annotated[
        str | None,
        Field(description="Filter by channel: 'mcp', 'rest', 'dashboard'."),
    ] = None,
    tool_name: Annotated[str | None, Field(description="Filter by a single tool.")] = None,
    limit: Annotated[int, Field(description="How many records (1-200).", ge=1, le=200)] = 50,
) -> str:
    return _render(await _request(
        "GET", f"{API_PREFIX}/usage/calls",
        params={
            "start_date": start_date, "end_date": end_date, "group_by": group_by,
            "client": client, "tool_name": tool_name, "limit": limit,
        },
    ))


@server.tool(
    description=(
        "Lists audit records: who changed what, and when. "
        "Consent changes and human overrides show up here."
    ),
    annotations=READ_ONLY,
)
async def humetric_audit_logs(
    action: Annotated[str | None, Field(description="Filter by action type.")] = None,
    entity_id: Annotated[str | None, Field(description="Filter by entity.")] = None,
    limit: Annotated[int, Field(description="How many records (1-200).", ge=1, le=200)] = 50,
    offset: Annotated[int, Field(description="Number of records to skip for pagination.", ge=0)] = 0,
) -> str:
    return _render(await _request(
        "GET", f"{API_PREFIX}/audit-logs",
        params={"action": action, "entity_id": entity_id, "limit": limit, "offset": offset},
    ))


@server.tool(
    description=(
        "Checks the service's health: API, database, and queue worker. "
        "If signals are stuck in 'queued', check here first — the worker may have stopped."
    ),
    annotations=READ_ONLY,
)
async def humetric_health() -> str:
    out: dict[str, Any] = {}
    for name, path in (("api", "/healthz"), ("database", "/healthz/db"), ("worker", "/healthz/worker")):
        try:
            out[name] = await _request("GET", path, authenticated=False)
        except ApiError as exc:
            out[name] = {"error": str(exc)}
    return _render(out)


# ── Resources ──────────────────────────────────────────────────────────────────

@server.resource(
    "humetric://packs",
    name="Metric Packs",
    description="All defined Metric Packs — which metrics are measured for which entity type.",
    mime_type="application/json",
)
async def packs_resource() -> str:
    return _render(await _request("GET", f"{API_PREFIX}/packs"))


@server.resource(
    "humetric://dashboard",
    name="Account summary",
    description="Tier, subscription status, and this month's usage.",
    mime_type="application/json",
)
async def dashboard_resource() -> str:
    return _render(await _request("GET", f"{API_PREFIX}/tenant/dashboard"))


# ── Ready-made workflows (prompts) ────────────────────────────────────────────

@server.prompt(
    name="analyze_entity",
    description="Evaluates an entity's metrics along with their supporting evidence.",
)
def analyze_entity_prompt(entity_id: str) -> str:
    return (
        f"Evaluate entity '{entity_id}'.\n\n"
        "1. Get the entity with humetric_get_entity and its metrics with "
        "humetric_get_entity_metrics.\n"
        "2. For the two lowest and two highest metrics, call "
        "humetric_explain_metric to see which signals the value rests on.\n"
        "3. Separately flag any metrics with confidence below 0.6.\n\n"
        "Write the result in this order: first a paragraph of overall "
        "assessment, then a metric table (metric | value | confidence), and "
        "finally, under a 'weak evidence' heading, the low-confidence metrics and why."
    )


@server.prompt(
    name="investigate_signal",
    description="Examines a signal's processing pipeline from start to finish.",
)
def investigate_signal_prompt(signal_id: str) -> str:
    return (
        f"Investigate signal '{signal_id}'.\n\n"
        "1. Check its status with humetric_get_signal.\n"
        "2. Get the extraction trace with humetric_get_signal_trace: what the "
        "extractor proposed, what the curator changed or rejected.\n"
        "3. If the signal is 'failed', find the error reason; if it's stuck "
        "in 'queued', check worker status with humetric_health.\n\n"
        "Walk through the findings in order and end with a one-sentence diagnosis."
    )


@server.prompt(
    name="draft_metric_pack",
    description="Drafts a new Metric Pack for a sector/role.",
)
def draft_metric_pack_prompt(entity_type: str, domain: str) -> str:
    return (
        f"Draft a Metric Pack of type '{entity_type}' for the '{domain}' domain.\n\n"
        "1. First review an existing pack with humetric_list_packs and "
        "humetric_get_pack; take the YAML format and field names from there.\n"
        "2. Propose 6-10 metrics. For each: key (snake_case), label, type, "
        "extraction prompt, sensitive flag, and default_confidence.\n"
        "3. Mark metrics containing personal/sensitive data as sensitive: true "
        "and list them in the kvkk section.\n\n"
        "Show me the YAML and WAIT FOR APPROVAL. Don't call humetric_create_pack "
        "before it's approved."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="HuMetric MCP server")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio")
    parser.add_argument("--port", type=int, default=8765, help="port for sse/streamable-http")
    args = parser.parse_args()

    if not _api_key():
        # Not fatal: the server starts up and explains the error on tool call.
        # Still, warning at startup lets you spot the problem without asking Claude.
        _log.warning(
            "HUMETRIC_MCP_API_KEY is not set — tool calls will return an "
            "authentication error."
        )
    _log.info("Starting HuMetric MCP server (transport=%s, api=%s)", args.transport, BASE_URL)

    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.settings.port = args.port
        server.run(transport=args.transport)


if __name__ == "__main__":
    main()
