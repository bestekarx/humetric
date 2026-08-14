"""Smoke test for the MCP server (Spec 026) — catches import/instantiation regressions."""

import os

os.environ.setdefault("HUMETRIC_MCP_API_KEY", "hm_test_dummy")

from humetric import mcp_server  # noqa: E402

EXPECTED_TOOLS = {
    "humetric_ingest_signal",
    "humetric_get_signal",
    "humetric_get_signal_trace",
    "humetric_list_entity_signals",
    "humetric_upsert_entity",
    "humetric_get_entity",
    "humetric_list_entities",
    "humetric_get_entity_metrics",
    "humetric_explain_metric",
    "humetric_metric_history",
    "humetric_query_entities",
    "humetric_list_packs",
    "humetric_get_pack",
    "humetric_create_pack",
    "humetric_update_pack",
    "humetric_list_pending_review",
    "humetric_review_metric",
    "humetric_get_consent",
    "humetric_grant_consent",
    "humetric_revoke_consent",
    "humetric_dashboard",
    "humetric_usage_report",
    "humetric_audit_logs",
    "humetric_health",
}


async def test_server_lists_expected_tools():
    tools = await mcp_server.server.list_tools()
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOLS


async def test_server_lists_expected_resources():
    resources = await mcp_server.server.list_resources()
    uris = {str(r.uri) for r in resources}
    assert "humetric://packs" in uris
    assert "humetric://dashboard" in uris


async def test_server_lists_expected_prompts():
    prompts = await mcp_server.server.list_prompts()
    names = {p.name for p in prompts}
    assert {"analyze_entity", "investigate_signal", "draft_metric_pack"} <= names
