"""Metric Analyzer agent — autonomous schema/image/market scan + findings (Spec 027 Faz 1).

Runs on Fable via the raw ``client.beta.messages.create`` API (not
``structured_call``): the analysis pass needs the MCP connector (Gentic
research tools) and a free-text report, and the findings pass needs
``output_config.format`` structured output — combining forced tool_choice
(``structured_call``'s approach) with either would conflict with citations
and with Fable's always-on thinking.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import anthropic

from .. import config, telemetry
from ..schema import AnalyzerFindings, PackDefinition
from . import _load_prompt

_log = logging.getLogger(__name__)

_ANALYSIS_SYSTEM = _load_prompt("analyzer-system") or (
    "You are the HuMetric Metric Analyzer. Analyze the given application "
    "description, schema, and screenshots, then report the theme you "
    "understood, open questions, and proposed metrics."
)
_FINDINGS_SYSTEM = _load_prompt("analyzer-findings") or (
    "Extract a structured AnalyzerFindings object from the analysis report."
)


def _gentic_mcp_params() -> dict:
    """MCP connector params for the Gentic research server, or {} if disabled.

    Empty GENTIC_API_KEY is the graceful-degradation switch: the analyzer
    runs schema/image-only, no market/idea research, no MCP beta header.
    """
    if not config.GENTIC_API_KEY:
        return {}
    return {
        "mcp_servers": [
            {
                "type": "url",
                "name": "gentic-research",
                "url": config.GENTIC_MCP_URL,
                "authorization_token": config.GENTIC_API_KEY,
            }
        ],
        "tools": [
            {"type": "mcp_toolset", "mcp_server_name": "gentic-research"},
        ],
        "betas": ["mcp-client-2025-11-20"],
    }


async def _fable_call(
    *,
    system: str,
    messages: list[dict],
    tenant_id: int | None,
    api_key: str | None,
    with_research: bool,
    output_config_format: dict | None = None,
):
    """One ``client.beta.messages.create`` call with Fable's fixed shape.

    No ``thinking``/``temperature``/``top_p``/``top_k``/prefill — Fable's
    always-on thinking is controlled solely via ``output_config.effort``.
    """
    from .base import _get_client

    client = _get_client(api_key=api_key)

    betas: list[str] = ["server-side-fallback-2026-06-01"]
    extra_params: dict = {}
    if with_research:
        research = _gentic_mcp_params()
        if research:
            extra_params["mcp_servers"] = research["mcp_servers"]
            extra_params["tools"] = research["tools"]
            betas.extend(research["betas"])

    if config.PROMPT_CACHE_ENABLED:
        system_param: object = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]
    else:
        system_param = system

    output_config: dict = {"effort": config.ANALYZER_EFFORT}
    if output_config_format is not None:
        output_config["format"] = output_config_format

    def _do_call(include_fallbacks: bool):
        kwargs: dict = dict(
            model=config.ANALYZER_MODEL,
            max_tokens=config.ANALYZER_MAX_TOKENS,
            system=system_param,
            messages=messages,
            betas=betas,
            output_config=output_config,
            **extra_params,
        )
        if include_fallbacks:
            kwargs["fallbacks"] = [{"model": config.ANALYZER_FALLBACK_MODEL}]
        return client.beta.messages.create(**kwargs)

    t0 = time.perf_counter()
    try:
        resp = await asyncio.to_thread(_do_call, True)
    except anthropic.BadRequestError as exc:
        if extra_params.get("mcp_servers"):
            _log.warning(
                "Analyzer call with fallbacks+MCP betas rejected (400), "
                "retrying without fallbacks: %s", exc,
            )
            resp = await asyncio.to_thread(_do_call, False)
        else:
            raise

    latency_ms = int((time.perf_counter() - t0) * 1000)
    telemetry.log_call(
        agent="analyzer", model=config.ANALYZER_MODEL, usage=resp.usage, latency_ms=latency_ms,
    )

    if tenant_id is not None:
        try:
            total_tokens = (resp.usage.input_tokens if resp.usage else 0) + (
                resp.usage.output_tokens if resp.usage else 0
            )
            if total_tokens > 0:
                from ..services.usage_service import record_llm_tokens
                await record_llm_tokens(tenant_id, total_tokens)
        except Exception:
            _log.exception("Failed to record LLM tokens for tenant %s", tenant_id)

    return resp


async def run_analysis(
    user_content: list[dict],
    *,
    tenant_id: int | None,
    api_key: str | None,
) -> str:
    """Autonomous analysis pass. Returns the concatenated text of the report.

    ``pause_turn`` is looped in-memory (not persisted) up to
    ``ANALYZER_PAUSE_TURN_MAX`` times — the model resumes a long-running turn
    (e.g. mid multi-step research) on its own; only the resulting text blocks
    are kept in the session's report.
    """
    messages: list[dict] = [{"role": "user", "content": user_content}]
    report_parts: list[str] = []

    for _ in range(config.ANALYZER_PAUSE_TURN_MAX):
        resp = await _fable_call(
            system=_ANALYSIS_SYSTEM,
            messages=messages,
            tenant_id=tenant_id,
            api_key=api_key,
            with_research=True,
        )

        if resp.stop_reason == "refusal":
            raise ValueError("analysis_refused")

        for block in resp.content:
            if block.type == "text":
                report_parts.append(block.text)

        if resp.stop_reason == "pause_turn":
            messages = [*messages, {"role": "assistant", "content": resp.content}]
            continue
        break

    return "\n\n".join(p for p in report_parts if p)


async def extract_findings(
    schema_text: str | None,
    report_text: str,
    *,
    tenant_id: int | None,
    api_key: str | None,
) -> AnalyzerFindings:
    """Fresh, tool-free call: schema text + analysis report -> AnalyzerFindings."""
    if schema_text:
        user_text = f"Schema:\n{schema_text}\n\nAnalysis report:\n{report_text}"
    else:
        user_text = f"Analysis report:\n{report_text}"

    resp = await _fable_call(
        system=_FINDINGS_SYSTEM,
        messages=[{"role": "user", "content": [{"type": "text", "text": user_text}]}],
        tenant_id=tenant_id,
        api_key=api_key,
        with_research=False,
        output_config_format={
            "type": "json_schema",
            "schema": AnalyzerFindings.model_json_schema(),
        },
    )

    if resp.stop_reason == "refusal":
        raise ValueError("findings_refused")

    for block in resp.content:
        if block.type == "text":
            data = json.loads(block.text)
            return AnalyzerFindings.model_validate(data)

    raise RuntimeError(f"Findings extraction produced no text block: {resp.content!r}")


def findings_to_pack_definition(findings: AnalyzerFindings) -> dict:
    """Map AnalyzerFindings onto a validated PackDefinition dict.

    Drops the analysis-only fields (summary/open_questions/market_notes/
    rationale) that have no place in a Metric Pack.
    """
    metrics = [
        {
            "key": m.key,
            "label": m.label,
            "type": m.type,
            "prompt": m.prompt,
            "default_confidence": m.default_confidence,
            "sensitive": m.sensitive,
            "requires_consent_scope": m.requires_consent_scope,
        }
        for m in findings.metrics
    ]
    sensitive_keys = [m.key for m in findings.metrics if m.sensitive]
    required_fields = [
        {"key": f.key, "type": f.type, "label": f.label} for f in findings.required_fields
    ]

    pack_dict = {
        "entity_type": findings.entity_type,
        "label": findings.label,
        "version": 1,
        "required_fields": required_fields,
        "metrics": metrics,
        "prompts": {"extraction": findings.extraction_prompt, "curation": findings.curation_prompt},
        "kvkk": {"sensitive_metrics": sensitive_keys},
    }
    validated = PackDefinition.model_validate(pack_dict)
    return validated.model_dump(exclude_none=True)


async def process_analysis_scan_task(db, task) -> None:
    """Worker entry point for the ``analysis_scan`` task type."""
    from ..store import Store
    from .base import get_tenant_llm_config

    payload = task.payload or {}
    mode = payload.get("mode", "initial")
    session_id = payload.get("session_id")

    session = await Store.get_analysis_session(db, session_id, task.tenant_id)
    if not session:
        _log.warning(
            "analysis_scan: session %s not found (tenant=%s) — deleted session, no-op",
            session_id, task.tenant_id,
        )
        return
    if session.status != "processing":
        _log.info(
            "analysis_scan: session %s not in 'processing' (status=%s) — reclaim idempotency, no-op",
            session_id, session.status,
        )
        return

    _provider, api_key = await get_tenant_llm_config(task.tenant_id, db)

    schema_text: str | None = None
    schema_format: str | None = None
    description_text = ""
    for artifact in session.artifacts:
        if artifact.get("kind") == "schema":
            schema_text = artifact.get("text")
            schema_format = artifact.get("format")
        elif artifact.get("kind") == "description":
            description_text = artifact.get("text", "")

    now_iso = datetime.now(timezone.utc).isoformat()

    if mode == "initial":
        user_content: list[dict] = [
            {"type": "text", "text": f"Application description:\n{description_text}"}
        ]
        if schema_text:
            user_content.append({
                "type": "text",
                "text": f"Database schema ({schema_format or 'unknown'}):\n{schema_text}",
            })
        for artifact in session.artifacts:
            if artifact.get("kind") == "image":
                user_content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": artifact.get("media_type"),
                        "data": artifact.get("data_b64"),
                    },
                })

        report_text = await run_analysis(user_content, tenant_id=task.tenant_id, api_key=api_key)
        session.report = [*session.report, {"kind": "analysis", "text": report_text, "ts": now_iso}]

        findings = await extract_findings(
            schema_text, report_text, tenant_id=task.tenant_id, api_key=api_key,
        )
        session.findings = findings.model_dump()
        session.status = "findings_ready"

    else:  # refine
        user_input = payload.get("user_input", "")
        prev_report_text = "\n\n".join(s.get("text", "") for s in session.report)
        prev_findings_json = json.dumps(session.findings or {}, ensure_ascii=False)
        refine_content = (
            f"Previous analysis report:\n{prev_report_text}\n\n"
            f"Previous findings (JSON):\n{prev_findings_json}\n\n"
            f"User response / correction:\n{user_input}"
        )

        report_text = await run_analysis(
            [{"type": "text", "text": refine_content}], tenant_id=task.tenant_id, api_key=api_key,
        )
        session.report = [
            *session.report, {"kind": "refine_analysis", "text": report_text, "ts": now_iso},
        ]

        findings = await extract_findings(
            schema_text, report_text, tenant_id=task.tenant_id, api_key=api_key,
        )
        session.findings = findings.model_dump()
        session.refine_count = session.refine_count + 1
        session.status = "findings_ready"

    session.updated_at = datetime.now(timezone.utc)
    db.add(session)
    await db.commit()
