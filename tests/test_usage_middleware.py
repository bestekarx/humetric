"""UsageMiddleware + /v1/usage/calls testleri.

Odak: hangi isteklerin kaydedildigi, istemciden gelen aciklayici header'larin
dogrulanmasi ve call_id gruplamasinin fan-out'u dogru saymasi.
"""

from __future__ import annotations

import pytest
from starlette.datastructures import Headers

from humetric.middleware.usage import (
    _normalise_call_id,
    _normalise_client,
    _normalise_tool,
    UsageMiddleware,
)
from humetric.store import Store


# ── Header dogrulama ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("mcp", "mcp"),
    ("MCP", "mcp"),
    (" dashboard ", "dashboard"),
    ("rest", "rest"),
    # Beyaz liste disinda kalan her sey None: yoksa uydurma bir kanal adi
    # client'a gore gruplayan her raporu boler.
    ("hacker", None),
    ("mcp; DROP TABLE usage_record", None),
    ("", None),
    (None, None),
])
def test_normalise_client_whitelist(raw, expected):
    assert _normalise_client(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("humetric_list_packs", "humetric_list_packs"),
    ("  humetric_health  ", "humetric_health"),
    ("DROP TABLE", None),
    ("humetric-list-packs", None),   # tire yok, tool adlari snake_case
    ("x" * 65, None),                # kolon 64 karakter
    ("", None),
    (None, None),
])
def test_normalise_tool_pattern(raw, expected):
    assert _normalise_tool(raw) == expected


def test_normalise_call_id_truncates():
    assert _normalise_call_id("a" * 100) == "a" * 64
    assert _normalise_call_id("  abc  ") == "abc"
    assert _normalise_call_id("   ") is None
    assert _normalise_call_id(None) is None


# ── Kaydetme davranisi ────────────────────────────────────────────────────────


class _FakeRequest:
    """Middleware'in okudugu yuzeyi tasiyan minimal sahte istek."""

    def __init__(self, path, *, method="GET", headers=None, state=None, route_path=None):
        self.method = method
        self.headers = Headers(headers or {})
        self.scope = {"route": _FakeRoute(route_path)} if route_path else {}
        self.state = state or _FakeState()
        self.url = type("U", (), {"path": path})()


class _FakeRoute:
    def __init__(self, path):
        self.path = path


class _FakeState:
    def __init__(self, tenant_id=None, api_key_id=None):
        if tenant_id is not None:
            self.tenant_id = tenant_id
        if api_key_id is not None:
            self.api_key_id = api_key_id


class _FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


async def _dispatch(request, captured, status_code=200):
    """Middleware'i kuyruga yazmadan calistir; satiri yakala."""
    mw = UsageMiddleware(app=None)

    async def call_next(_req):
        return _FakeResponse(status_code)

    def fake_ensure():
        class _Q:
            @staticmethod
            def put_nowait(row):
                captured.append(row)
        return _Q()

    import humetric.middleware.usage as usage_mod
    original = usage_mod._ensure_flusher
    usage_mod._ensure_flusher = fake_ensure
    try:
        return await mw.dispatch(request, call_next)
    finally:
        usage_mod._ensure_flusher = original


@pytest.mark.asyncio
async def test_authenticated_request_is_recorded():
    captured = []
    req = _FakeRequest(
        "/v1/packs",
        headers={
            "X-HuMetric-Client": "mcp",
            "X-HuMetric-Tool": "humetric_list_packs",
            "X-HuMetric-Call-Id": "abc123",
        },
        state=_FakeState(tenant_id=1, api_key_id=7),
        route_path="/v1/packs",
    )
    await _dispatch(req, captured)

    assert len(captured) == 1
    row = captured[0]
    assert row["tenant_id"] == 1
    assert row["api_key_id"] == 7
    assert row["client"] == "mcp"
    assert row["tool_name"] == "humetric_list_packs"
    assert row["call_id"] == "abc123"
    assert row["status_code"] == 200
    assert row["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_public_path_is_not_recorded():
    captured = []
    await _dispatch(_FakeRequest("/healthz"), captured)
    assert captured == []


@pytest.mark.asyncio
async def test_unauthenticated_request_is_not_recorded():
    """Auth reddettiginde atfedilecek bir tenant yok."""
    captured = []
    await _dispatch(_FakeRequest("/v1/packs", route_path="/v1/packs"), captured, status_code=401)
    assert captured == []


@pytest.mark.asyncio
async def test_spoofed_headers_are_dropped_but_row_is_kept():
    """Sahte header satiri dusurmez, yalnizca aciklayici alanlari bosaltir.

    Kimlik header'dan degil, auth'un cozdugu anahtardan geliyor — bir tenant en
    kotu ihtimalle kendi raporunu bulandirir.
    """
    captured = []
    req = _FakeRequest(
        "/v1/packs",
        headers={"X-HuMetric-Client": "hacker", "X-HuMetric-Tool": "DROP TABLE"},
        state=_FakeState(tenant_id=1, api_key_id=7),
        route_path="/v1/packs",
    )
    await _dispatch(req, captured)

    assert len(captured) == 1
    assert captured[0]["client"] is None
    assert captured[0]["tool_name"] is None
    assert captured[0]["tenant_id"] == 1


@pytest.mark.asyncio
async def test_endpoint_uses_route_template_not_raw_path():
    """Ham path her entity'yi ayri endpoint yapar ve gruplamayi imkansiz kilar."""
    captured = []
    req = _FakeRequest(
        "/v1/entities/acme-corp/metrics",
        state=_FakeState(tenant_id=1),
        route_path="/v1/entities/{entity_id}/metrics",
    )
    await _dispatch(req, captured)
    assert captured[0]["endpoint"] == "/v1/entities/{entity_id}/metrics"


@pytest.mark.asyncio
async def test_endpoint_falls_back_to_raw_path_when_unmatched():
    captured = []
    req = _FakeRequest("/v1/nope", state=_FakeState(tenant_id=1))
    await _dispatch(req, captured)
    assert captured[0]["endpoint"] == "/v1/nope"


# ── Rapor ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_call_report_counts_fan_out_once(test_db, tenant_id):
    """Ayni call_id'yi paylasan uc istek = bir tool cagrisi, uc HTTP istegi.

    humetric_health'in davranisi bu; call_id olmadan kullanicinin yaptigi tek
    cagri uc kez sayilirdi.
    """
    # Diger testler de middleware uzerinden ayni tenant'a satir yaziyor;
    # kendi satirlarimizi ayirt etmek icin sentinel bir client degeri.
    marker = "t-fanout"

    for path in ("/healthz", "/healthz/db", "/healthz/worker"):
        await Store.record_usage(test_db, {
            "tenant_id": tenant_id, "api_key_id": None, "endpoint": path,
            "method": "GET", "status_code": 200, "client": marker,
            "tool_name": "humetric_health", "call_id": "one-call", "duration_ms": 5,
        })
    # call_id'si olmayan duz bir REST istegi kendi basina bir cagridir.
    await Store.record_usage(test_db, {
        "tenant_id": tenant_id, "api_key_id": None, "endpoint": "/v1/packs",
        "method": "GET", "status_code": 500, "client": marker,
        "tool_name": None, "call_id": None, "duration_ms": 9,
    })

    from datetime import date
    today = date.today()
    rows = await Store.aggregate_usage_calls(
        test_db, tenant_id, start_date=today, end_date=today,
        group_by="tool", client=marker,
    )
    by_tool = {r["group"]: r for r in rows}

    assert by_tool["humetric_health"]["tool_calls"] == 1
    assert by_tool["humetric_health"]["http_requests"] == 3
    # call_id yoksa satir kendi id'sine dusuyor; aksi halde sayim 0 cikardi.
    assert by_tool["(none)"]["tool_calls"] == 1
    assert by_tool["(none)"]["error_count"] == 1


@pytest.mark.asyncio
async def test_call_report_rejects_unknown_group_by(async_client, test_db, test_tenant):
    # Ortak test_api_key fixture'inda tenant:admin yok; scope kontrolu
    # group_by dogrulamasindan once calistigi icin 403 gelirdi.
    admin_key, _ = await Store.create_api_key(
        test_db, tenant_id=test_tenant.id, prefix="hm_test",
        label="Admin", scopes=["tenant:admin"],
    )
    resp = await async_client.get(
        "/v1/usage/calls",
        params={"start_date": "2026-08-01", "end_date": "2026-08-31", "group_by": "; DROP"},
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_group_by"


@pytest.mark.asyncio
async def test_call_report_requires_tenant_admin_scope(async_client, test_db, test_tenant):
    """packs:admin yeterli degil; rapor tenant:admin istiyor."""
    narrow_key, _ = await Store.create_api_key(
        test_db, tenant_id=test_tenant.id, prefix="hm_test",
        label="Narrow", scopes=["packs:read"],
    )
    resp = await async_client.get(
        "/v1/usage/calls",
        params={"start_date": "2026-08-01", "end_date": "2026-08-31"},
        headers={"Authorization": f"Bearer {narrow_key}"},
    )
    assert resp.status_code == 403
