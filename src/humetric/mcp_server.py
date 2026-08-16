#!/usr/bin/env python3
"""HuMetric MCP Server — Claude entegrasyonu (Spec 026).

HuMetric REST API'sinin tamamini Claude'un kullanabilecegi tool'lara acar.
Transport: stdio (Claude Desktop) veya SSE / streamable-http (uzak host).

Kullanim:
  humetric-mcp                                  # stdio (pip install -e . sonrasi)
  humetric-mcp --transport sse --port 8765
  python -m humetric.mcp_server                 # kurulum yapmadan gelistirme

Yapilandirma (.env veya ortam degiskeni):
  HUMETRIC_MCP_API_KEY   zorunlu — hm_live_... API anahtari
  HUMETRIC_BASE_URL      varsayilan http://localhost:8002
  HUMETRIC_MCP_TIMEOUT_S varsayilan 30

--- Kapsam karari ------------------------------------------------------------
API'de 41 uc var; burada 24'u tool olarak acildi. Disarida birakilanlar ve
nedenleri (bilerek yapildi, eksik degil):

  /v1/register, /v1/login, /v1/verify-email
      Hesap acilisi. MCP oturumu zaten kimlik dogrulanmis bir anahtarla
      calisiyor; buradan yeni hesap acmak anlamsiz ve risklidir.
  /v1/api-keys (POST/DELETE), /v1/tenant/rotate-api-key
      Kimlik bilgisi uretme/iptal. Bir agent'in kendi erisim anahtarlarini
      yonetebilmesi, yetki sinirlamasini anlamsiz kilar.
  /v1/tenant/keys (GET/PUT/DELETE)
      BYOK saglayici sirlari. Okunmasi da yazilmasi da agent isi degil.
  /v1/billing/checkout, /v1/billing/webhook
      Para hareketi ve Stripe callback'i.
  /v1/admin/usage
      Tenant sinirlari otesinde admin raporu.
  /v1/packs/wizard
      YAML'i LLM'e yazdiran uc. Buradaki LLM zaten Claude — pack'i kendisi
      yazip humetric_create_pack ile yayinlamasi hem daha ucuz hem daha iyi.
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

# stdio transport'ta stdout SADECE protokol mesajlari icindir; oraya dusen tek
# bir satir baglantiyi bozar. Tum loglar stderr'e.
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

# Buyuk listeler Claude'un baglamini gereksiz doldurmasin; kirpilan miktar
# yanitta acikca belirtilir ki eksik veriyi tam sansin.
MAX_ITEMS = int(os.environ.get("HUMETRIC_MCP_MAX_ITEMS", "50"))


def _api_key() -> str:
    """Anahtari cagri aninda oku.

    Bilerek modul yuklenirken kontrol edilmiyor: eski surum burada sys.exit(1)
    cagiriyordu ve Claude Desktop bunu yalnizca "server failed to start" diye
    gosteriyor, sebebi kullaniciya hic ulasmiyordu. Boylece sunucu ayaga
    kalkiyor ve eksik yapilandirmayi ilk tool cagrisinda duz Turkce anlatiyor.
    """
    return os.environ.get("HUMETRIC_MCP_API_KEY", "").strip()


_client: httpx.AsyncClient | None = None


async def _get_client() -> httpx.AsyncClient:
    """Tek bir AsyncClient paylasilir — her cagrida yeni baglanti havuzu acmak
    hem yavas hem de TLS el sikismasini bosuna tekrarlar."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=httpx.Timeout(TIMEOUT_S),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )
    return _client


class ApiError(Exception):
    """API'nin dondugu, kullaniciya gosterilebilir hata."""


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
            "HUMETRIC_MCP_API_KEY tanimli degil. Claude Desktop yapilandirmasindaki "
            "env blogana HuMetric API anahtarinizi (hm_live_...) ekleyip uygulamayi "
            "yeniden baslatin."
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
        raise ApiError(f"API JSON olmayan yanit dondu (HTTP {resp.status_code}).")

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
    """Istegi gonder; tasima katmani hatasinda bir kez yeniden dene.

    Neden gerekli: API 5xx dondugunde baglantiyi kapatabiliyor, ama havuzdaki
    keep-alive girdisi canli sanilmaya devam ediyor. Bir sonraki istek o olu
    baglantiyi kullanip httpx.ReadError aliyor — ustelik bu istisnanin mesaji
    BOS, yani kullaniciya "hata olustu: " diye bos bir satir gidiyordu. Havuzu
    tazeleyip bir kez tekrar denemek dogru davranis; ikinci kez basarisiz
    olursa artik gercek bir sorun var demektir.
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
                f"HuMetric API {TIMEOUT_S:.0f} saniyede yanit vermedi ({path})."
            ) from exc
        except httpx.TransportError as exc:
            last_exc = exc
            _log.warning(
                "Baglanti hatasi (%s), deneme %d/2: %s", type(exc).__name__, attempt, exc or "(bos mesaj)"
            )
            await _reset_client()

    raise ApiError(
        f"HuMetric API'ye ulasilamadi ({BASE_URL}{path}): "
        f"{type(last_exc).__name__}. Servis calisiyor mu, HUMETRIC_BASE_URL dogru mu?"
    ) from last_exc


async def _reset_client() -> None:
    """Baglanti havuzunu kapat; sonraki cagri temiz bir client acar."""
    global _client
    if _client is not None and not _client.is_closed:
        try:
            await _client.aclose()
        except Exception:  # noqa: BLE001 - kapatma hatasi asil sorunu gizlemesin
            pass
    _client = None


def _clean_params(params: dict[str, Any] | None) -> dict[str, Any] | None:
    """None degerleri at — httpx onlari "None" stringi olarak gonderirdi."""
    if not params:
        return None
    cleaned = {k: v for k, v in params.items() if v is not None}
    return cleaned or None


def _describe_error(status: int, data: Any) -> str:
    """HuMetric'in hata zarfini ({"error": {...}}) okunur bir cumleye cevir."""
    envelope = data.get("error") if isinstance(data, dict) else None
    if isinstance(envelope, dict):
        code = envelope.get("code", "error")
        message = envelope.get("message", "")
        doc = envelope.get("doc_url")
        text = f"[{status} {code}] {message}"
        return f"{text} — {doc}" if doc else text

    # FastAPI dogrulama hatasi: hangi alanin neden reddedildigi burada.
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
    """Yaniti Claude icin kompakt JSON'a cevir, uzun listeleri kirp.

    Kirpma sessiz yapilmaz: kac ogenin gizlendigi yanitin icine yazilir, yoksa
    Claude elindekini tam liste sanip yanlis sonuca varir.
    """
    data = _truncate(data, max_items)
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _truncate(data: Any, max_items: int) -> Any:
    if isinstance(data, list):
        if len(data) > max_items:
            return data[:max_items] + [
                {"_kirpildi": f"{len(data) - max_items} oge daha var; "
                              f"limit/offset ile sayfalayin"}
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
HuMetric: Sinyallerden varlik metrikleri ureten agentik metrik motoru.

HuMetric, serbest metin veya yapilandirilmis "sinyal"lerden varliklar (entity)
hakkinda metrik cikaran bir servistir. Tipik akis:

  1. Metric Pack, hangi entity tipinde hangi metriklerin olcelecegini tanimlar.
  2. Bir varlik hakkinda sinyal gonderilir (humetric_ingest_signal).
  3. Sinyal ASENKRON islenir: yanittaki status genelde "queued" olur.
     Metrikleri hemen sorgulamayin — humetric_get_signal ile durumu
     "completed" olana kadar izleyin, sonra metrikleri okuyun.
  4. Metrikler okunur (humetric_get_entity_metrics) veya varliklar metrige
     gore siralanir (humetric_query_entities).

Calisma kurallari:

* Once kesfet: entity tipini veya metrik anahtarini bilmiyorsan
  humetric_list_packs ile mevcut pack'lere bak. Metrik anahtarlarini tahmin
  etme — pack'te tanimli olmayan metrik uretilmez.
* Bir metrigin degerini savunman gerekiyorsa humetric_explain_metric kullan;
  hangi sinyallerin o degere katkida bulundugunu dondurur. Zaman icindeki
  degisim icin humetric_metric_history.
* Guven skoru (confidence) dusuk metrikleri kesin gercek gibi sunma; deger
  yaninda confidence'i da aktar.
* KVKK: hassas kabul edilen metrikler ancak ilgili varlik icin acik riza
  varsa islenir. Hassas veri uretecek bir sinyal gondermeden once
  humetric_get_consent ile durumu kontrol et; riza yoksa kullaniciya soyle,
  kendi basina humetric_grant_consent cagirma — riza veri sahibinin beyanidir,
  senin varsayimin degil.
* Yazma islemleri (sinyal gonderme, pack yayinlama, metrik override) gercek
  veriyi degistirir. Kullanicinin acik talebi olmadan yapma.
* Bir tool hata dondurdugunde mesaji oldugu gibi aktar; hata kodlari
  (ornegin tier_limit_exceeded, consent_required) kullanici icin anlamlidir.
""".strip(),
)


# ── Sinyaller ─────────────────────────────────────────────────────────────────

@server.tool(
    description=(
        "Bir varlik hakkinda sinyal gonderir; metrik cikarimi asenkron baslar. "
        "Yanittaki signal_id ile durumu humetric_get_signal'dan izleyin — "
        "status 'completed' olmadan metrikler guncel olmaz."
    ),
    annotations=WRITES,
)
async def humetric_ingest_signal(
    entity_id: Annotated[str, Field(description="Varligin kimligi. Yoksa varlik otomatik olusturulur.")],
    entity_type: Annotated[str, Field(description="Varlik tipi; bir Metric Pack'te tanimli olmali (or. 'support_agent').")],
    text: Annotated[str | None, Field(description="Serbest metin sinyal: yorum, not, transkript, geri bildirim.")] = None,
    structured: Annotated[dict[str, Any] | None, Field(description="Yapilandirilmis sinyal alanlari (sayisal/kategorik veri).")] = None,
    external_id: Annotated[str | None, Field(description="Kaynak sistemdeki kimlik. Ayni id tekrar gonderilirse sinyal kopyalanmaz.")] = None,
    occurred_at: Annotated[str | None, Field(description="Olayin gerceklestigi an (ISO-8601). Gecmise donuk yukleme icin; bos birakilirsa 'simdi'.")] = None,
) -> str:
    if not text and not structured:
        raise ApiError("text veya structured'dan en az biri verilmeli — bos sinyalden metrik cikmaz.")
    body = {"entity_id": entity_id, "entity_type": entity_type}
    for key, value in (("text", text), ("structured", structured),
                       ("external_id", external_id), ("occurred_at", occurred_at)):
        if value is not None:
            body[key] = value
    return _render(await _request("POST", f"{API_PREFIX}/signals", json_body=body))


@server.tool(
    description="Bir sinyalin islenme durumunu dondurur (queued / processing / completed / failed).",
    annotations=READ_ONLY,
)
async def humetric_get_signal(
    signal_id: Annotated[str, Field(description="humetric_ingest_signal'in dondugu signal_id.")],
) -> str:
    return _render(await _request("GET", f"{API_PREFIX}/signals/{signal_id}"))


@server.tool(
    description=(
        "Bir sinyalin islenme izini dondurur: hangi agent ne cikardi, kurator "
        "neyi nasil degistirdi. Bir metrigin neden o deger oldugunu tartisirken kullanin."
    ),
    annotations=READ_ONLY,
)
async def humetric_get_signal_trace(
    signal_id: Annotated[str, Field(description="Izi alinacak sinyalin kimligi.")],
) -> str:
    return _render(await _request("GET", f"{API_PREFIX}/signals/{signal_id}/trace"))


@server.tool(
    description="Bir varliga ait sinyalleri listeler (en yeniden eskiye).",
    annotations=READ_ONLY,
)
async def humetric_list_entity_signals(
    entity_id: Annotated[str, Field(description="Varlik kimligi.")],
    status: Annotated[str | None, Field(description="Duruma gore filtre: queued, processing, completed, failed.")] = None,
    limit: Annotated[int, Field(description="Kac kayit dondurulsun (1-200).", ge=1, le=200)] = 20,
    offset: Annotated[int, Field(description="Sayfalama icin atlanacak kayit sayisi.", ge=0)] = 0,
) -> str:
    return _render(await _request(
        "GET", f"{API_PREFIX}/entities/{entity_id}/signals",
        params={"status": status, "limit": limit, "offset": offset},
    ))


# ── Varliklar ve metrikler ────────────────────────────────────────────────────

@server.tool(
    description=(
        "Varlik olusturur veya gunceller (upsert). Sinyal gondermek varligi zaten "
        "otomatik olusturur; bunu yalnizca ad/segment gibi alanlari onceden "
        "doldurmak istediginizde kullanin."
    ),
    annotations=WRITES,
)
async def humetric_upsert_entity(
    id: Annotated[str, Field(description="Varlik kimligi (sizin sisteminizdeki id).")],
    entity_type: Annotated[str, Field(description="Varlik tipi; bir Metric Pack'te tanimli olmali.")],
    fields: Annotated[dict[str, Any] | None, Field(description="Yapisal alanlar: ad, departman, segment vb.")] = None,
    free_text: Annotated[str | None, Field(description="Varligi tanimlayan serbest metin (arama/eslestirmede kullanilir).")] = None,
    status: Annotated[str, Field(description="Varlik durumu: active veya inactive.")] = "active",
) -> str:
    body: dict[str, Any] = {"id": id, "entity_type": entity_type, "status": status}
    if fields is not None:
        body["fields"] = fields
    if free_text is not None:
        body["free_text"] = free_text
    return _render(await _request("POST", f"{API_PREFIX}/entities", json_body=body))


@server.tool(
    description="Tek bir varligi alanlariyla birlikte dondurur.",
    annotations=READ_ONLY,
)
async def humetric_get_entity(
    entity_id: Annotated[str, Field(description="Varlik kimligi.")],
) -> str:
    return _render(await _request("GET", f"{API_PREFIX}/entities/{entity_id}"))


@server.tool(
    description="Varliklari listeler. Hangi varliklarin kayitli oldugunu kesfetmek icin.",
    annotations=READ_ONLY,
)
async def humetric_list_entities(
    entity_type: Annotated[str | None, Field(description="Tipe gore filtre (or. 'driver').")] = None,
    limit: Annotated[int, Field(description="Kac kayit dondurulsun (1-200).", ge=1, le=200)] = 50,
    offset: Annotated[int, Field(description="Sayfalama icin atlanacak kayit sayisi.", ge=0)] = 0,
) -> str:
    return _render(await _request(
        "GET", f"{API_PREFIX}/entities",
        params={"entity_type": entity_type, "limit": limit, "offset": offset},
    ))


@server.tool(
    description=(
        "Bir varligin tum metriklerini deger ve guven skoruyla dondurur. "
        "Guven skoru dusuk metrikleri kesinlik gibi sunmayin."
    ),
    annotations=READ_ONLY,
)
async def humetric_get_entity_metrics(
    entity_id: Annotated[str, Field(description="Varlik kimligi.")],
) -> str:
    return _render(await _request("GET", f"{API_PREFIX}/entities/{entity_id}/metrics"))


@server.tool(
    description=(
        "Bir metrigin neden o degerde oldugunu aciklar: degere katkida bulunan "
        "sinyaller, model ve gerekce. 'Bu skor nereden geliyor?' sorusunun cevabi."
    ),
    annotations=READ_ONLY,
)
async def humetric_explain_metric(
    entity_id: Annotated[str, Field(description="Varlik kimligi.")],
    metric_key: Annotated[str, Field(description="Metrik anahtari (or. 'resolution_speed').")],
) -> str:
    return _render(await _request(
        "GET", f"{API_PREFIX}/entities/{entity_id}/metrics/{metric_key}/explain"
    ))


@server.tool(
    description=(
        "Bir metrigin zaman icindeki degisimini dondurur. Trend sorularinda "
        "('gelisiyor mu, geriliyor mu') bunu kullanin."
    ),
    annotations=READ_ONLY,
)
async def humetric_metric_history(
    entity_id: Annotated[str, Field(description="Varlik kimligi.")],
    metric_key: Annotated[str, Field(description="Metrik anahtari.")],
    since: Annotated[str | None, Field(description="Baslangic ani (ISO-8601).")] = None,
    until: Annotated[str | None, Field(description="Bitis ani (ISO-8601).")] = None,
    limit: Annotated[int, Field(description="Kac nokta dondurulsun (1-500).", ge=1, le=500)] = 100,
    offset: Annotated[int, Field(description="Sayfalama icin atlanacak nokta sayisi.", ge=0)] = 0,
) -> str:
    return _render(await _request(
        "GET", f"{API_PREFIX}/entities/{entity_id}/metrics/{metric_key}/history",
        params={"since": since, "until": until, "limit": limit, "offset": offset},
    ))


# ── Sorgulama ─────────────────────────────────────────────────────────────────

@server.tool(
    description=(
        "Varliklari bir metrige gore siralar ve/veya serbest metinle arar. "
        "'En iyi 5 X kim', 'su ozelliğe sahip varliklari bul' turu sorularin araci. "
        "rank_by verilirse siralama o metrige gore, free_text_query verilirse "
        "anlamsal benzerlige gore yapilir; ikisi birlikte de kullanilabilir."
    ),
    annotations=READ_ONLY,
)
async def humetric_query_entities(
    entity_type: Annotated[str | None, Field(description="Aranacak varlik tipi.")] = None,
    rank_by: Annotated[str | None, Field(description="Siralama yapilacak metrik anahtari.")] = None,
    free_text_query: Annotated[str | None, Field(description="Anlamsal arama metni (or. 'sabirli ve detayci').")] = None,
    filters: Annotated[dict[str, Any] | None, Field(description="Alan bazli filtreler (or. {'department': 'destek'}).")] = None,
    top_k: Annotated[int, Field(description="Kac sonuc dondurulsun (1-100).", ge=1, le=100)] = 10,
    include_reasoning: Annotated[bool, Field(description="Her sonuc icin siralama gerekcesi de dondurulsun mu. Token maliyetini artirir.")] = False,
) -> str:
    body: dict[str, Any] = {"top_k": top_k, "include_reasoning": include_reasoning}
    for key, value in (("entity_type", entity_type), ("rank_by", rank_by),
                       ("free_text_query", free_text_query), ("filters", filters)):
        if value is not None:
            body[key] = value
    return _render(await _request("POST", f"{API_PREFIX}/query", json_body=body))


# ── Metric Pack'ler ───────────────────────────────────────────────────────────

@server.tool(
    description=(
        "Tanimli Metric Pack'leri listeler. Hangi entity tiplerinin ve metrik "
        "anahtarlarinin mevcut oldugunu ogrenmek icin ILK bakilacak yer."
    ),
    annotations=READ_ONLY,
)
async def humetric_list_packs(
    is_active: Annotated[bool | None, Field(description="Yalnizca aktif (True) veya pasif (False) pack'ler. Bos: hepsi.")] = None,
) -> str:
    return _render(await _request("GET", f"{API_PREFIX}/packs", params={"is_active": is_active}))


@server.tool(
    description="Bir Metric Pack'in tam tanimini (YAML ve metrik listesi) dondurur.",
    annotations=READ_ONLY,
)
async def humetric_get_pack(
    pack_key: Annotated[str, Field(description="Pack anahtari (or. 'support_agent_v1').")],
) -> str:
    return _render(await _request("GET", f"{API_PREFIX}/packs/{pack_key}"))


@server.tool(
    description=(
        "Yeni bir Metric Pack yayinlar. YAML'i siz yazarsiniz: entity_type, label, "
        "version, metrics[] (key, label, type, prompt, sensitive, default_confidence), "
        "prompts ve kvkk bolumleri gerekir. Once humetric_get_pack ile mevcut bir "
        "pack'i ornek alin — bicimi birebir eslesmelidir."
    ),
    annotations=WRITES,
)
async def humetric_create_pack(
    yaml_text: Annotated[str, Field(description="Pack tanimi, YAML metni olarak.")],
    pack_key: Annotated[str | None, Field(description="Pack anahtari. Verilmezse YAML'dan turetilir.")] = None,
) -> str:
    body: dict[str, Any] = {"yaml_text": yaml_text}
    if pack_key:
        body["pack_key"] = pack_key
    return _render(await _request("POST", f"{API_PREFIX}/packs", json_body=body))


@server.tool(
    description=(
        "Var olan bir Metric Pack'i gunceller (yeni surum olusturur). Metrik "
        "tanimini degistirmek gelecekteki cikarimlari etkiler, gecmis metrikleri "
        "geriye donuk degistirmez."
    ),
    annotations=WRITES,
)
async def humetric_update_pack(
    pack_key: Annotated[str, Field(description="Guncellenecek pack'in anahtari.")],
    yaml_text: Annotated[str, Field(description="Pack'in yeni tam tanimi (YAML).")],
) -> str:
    return _render(await _request(
        "PUT", f"{API_PREFIX}/packs/{pack_key}", json_body={"yaml_text": yaml_text, "pack_key": pack_key}
    ))


# ── Insan onayi (reviewer override) ───────────────────────────────────────────

@server.tool(
    description=(
        "Insan onayi bekleyen metrikleri listeler — guven skoru esigin altinda "
        "kaldigi icin isaretlenmis olanlar."
    ),
    annotations=READ_ONLY,
)
async def humetric_list_pending_review() -> str:
    return _render(await _request("GET", f"{API_PREFIX}/metrics/pending-review"))


@server.tool(
    description=(
        "Bir metrigin degerini insan karariyla ezer. Bu, modelin cikarimini kalici "
        "olarak degistirir ve denetim kaydina yazilir — kullanicinin acik talimati "
        "olmadan cagirmayin, kendi tahmininizi 'duzeltme' diye yazmayin."
    ),
    annotations=DESTRUCTIVE,
)
async def humetric_review_metric(
    entity_id: Annotated[str, Field(description="Varlik kimligi.")],
    metric_key: Annotated[str, Field(description="Metrik anahtari.")],
    value: Annotated[float, Field(description="Insanin belirledigi yeni deger.")],
    confidence: Annotated[float, Field(description="Bu degere duyulan guven (0-1).", ge=0, le=1)],
    comment: Annotated[str | None, Field(description="Degisikligin gerekcesi; denetim kaydina yazilir.")] = None,
) -> str:
    body: dict[str, Any] = {"value": value, "confidence": confidence}
    if comment:
        body["comment"] = comment
    return _render(await _request(
        "PUT", f"{API_PREFIX}/metrics/{entity_id}/{metric_key}/review", json_body=body
    ))


# ── Riza (KVKK) ───────────────────────────────────────────────────────────────

@server.tool(
    description=(
        "Bir varligin riza durumunu dondurur. Hassas metrik uretecek bir sinyal "
        "gondermeden once buraya bakin."
    ),
    annotations=READ_ONLY,
)
async def humetric_get_consent(
    entity_id: Annotated[str, Field(description="Varlik kimligi.")],
) -> str:
    return _render(await _request("GET", f"{API_PREFIX}/consent/{entity_id}"))


@server.tool(
    description=(
        "Bir varlik icin riza kaydi olusturur. Riza veri sahibinin beyanidir: "
        "yalnizca kullanici size acikca 'rizasi var, kaydet' dediginde cagirin. "
        "Bir islemi calistirmak icin kendiniz riza uretmeyin."
    ),
    annotations=WRITES,
)
async def humetric_grant_consent(
    entity_id: Annotated[str, Field(description="Varlik kimligi.")],
    scope: Annotated[str, Field(description="Riza kapsami (or. 'hassas_veri_isleme').")],
    expires_at: Annotated[str | None, Field(description="Rizanin gecerlilik bitisi (ISO-8601). Bos: sureisiz.")] = None,
) -> str:
    body: dict[str, Any] = {"entity_id": entity_id, "scope": scope, "status": "granted"}
    if expires_at:
        body["expires_at"] = expires_at
    return _render(await _request("POST", f"{API_PREFIX}/consent", json_body=body))


@server.tool(
    description="Bir varligin rizasini geri ceker. Bundan sonra hassas metrik islenmez.",
    annotations=DESTRUCTIVE,
)
async def humetric_revoke_consent(
    entity_id: Annotated[str, Field(description="Varlik kimligi.")],
) -> str:
    return _render(await _request("DELETE", f"{API_PREFIX}/consent/{entity_id}"))


# ── Hesap ve denetim ──────────────────────────────────────────────────────────

@server.tool(
    description="Hesabin ozeti: tier, abonelik durumu, bu ayki kullanim ve tier limitleri.",
    annotations=READ_ONLY,
)
async def humetric_dashboard() -> str:
    return _render(await _request("GET", f"{API_PREFIX}/tenant/dashboard"))


@server.tool(
    description="Tarih araligina gore gunluk kullanim raporu: sinyal, LLM token ve embedding sayilari.",
    annotations=READ_ONLY,
)
async def humetric_usage_report(
    start_date: Annotated[str, Field(description="Baslangic tarihi (YYYY-AA-GG).")],
    end_date: Annotated[str, Field(description="Bitis tarihi (YYYY-AA-GG).")],
) -> str:
    return _render(await _request(
        "GET", f"{API_PREFIX}/usage", params={"start_date": start_date, "end_date": end_date}
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
        "Denetim kayitlarini listeler: kim, neyi, ne zaman degistirdi. "
        "Riza degisiklikleri ve insan override'lari burada gorunur."
    ),
    annotations=READ_ONLY,
)
async def humetric_audit_logs(
    action: Annotated[str | None, Field(description="Islem tipine gore filtre.")] = None,
    entity_id: Annotated[str | None, Field(description="Varliga gore filtre.")] = None,
    limit: Annotated[int, Field(description="Kac kayit (1-200).", ge=1, le=200)] = 50,
    offset: Annotated[int, Field(description="Sayfalama icin atlanacak kayit sayisi.", ge=0)] = 0,
) -> str:
    return _render(await _request(
        "GET", f"{API_PREFIX}/audit-logs",
        params={"action": action, "entity_id": entity_id, "limit": limit, "offset": offset},
    ))


@server.tool(
    description=(
        "Servisin sagligini kontrol eder: API, veritabani ve kuyruk isleyicisi. "
        "Sinyaller 'queued'de takildiysa once buraya bakin — worker durmus olabilir."
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


# ── Kaynaklar (resources) ─────────────────────────────────────────────────────

@server.resource(
    "humetric://packs",
    name="Metric Pack'ler",
    description="Tanimli tum Metric Pack'ler — hangi entity tipinde hangi metrikler olculuyor.",
    mime_type="application/json",
)
async def packs_resource() -> str:
    return _render(await _request("GET", f"{API_PREFIX}/packs"))


@server.resource(
    "humetric://dashboard",
    name="Hesap ozeti",
    description="Tier, abonelik durumu ve bu ayki kullanim.",
    mime_type="application/json",
)
async def dashboard_resource() -> str:
    return _render(await _request("GET", f"{API_PREFIX}/tenant/dashboard"))


# ── Hazir is akislari (prompts) ───────────────────────────────────────────────

@server.prompt(
    name="analyze_entity",
    description="Bir varligin metriklerini kanitlariyla birlikte degerlendirir.",
)
def analyze_entity_prompt(entity_id: str) -> str:
    return (
        f"'{entity_id}' varligini degerlendir.\n\n"
        "1. humetric_get_entity ile varligi, humetric_get_entity_metrics ile "
        "metriklerini al.\n"
        "2. En dusuk ve en yuksek iki metrik icin humetric_explain_metric "
        "cagirip degerin hangi sinyallere dayandigini gor.\n"
        "3. Guven skoru 0.6'nin altinda kalan metrikleri ayrica isaretle.\n\n"
        "Sonucu su duzende yaz: once bir paragraf genel degerlendirme, sonra "
        "metrik tablosu (metrik | deger | guven), en sonda 'zayif kanit' basligi "
        "altinda dusuk guvenli metrikler ve nedenleri."
    )


@server.prompt(
    name="investigate_signal",
    description="Bir sinyalin islenme surecini bastan sona inceler.",
)
def investigate_signal_prompt(signal_id: str) -> str:
    return (
        f"'{signal_id}' sinyalini incele.\n\n"
        "1. humetric_get_signal ile durumu kontrol et.\n"
        "2. humetric_get_signal_trace ile cikarim izini al: extractor ne onerdi, "
        "kurator neyi degistirdi veya reddetti.\n"
        "3. Sinyal 'failed' ise hata sebebini, 'queued'de takildiysa "
        "humetric_health ile worker durumunu kontrol et.\n\n"
        "Bulgulari sirayla anlat ve sonunda tek cumlelik bir teshis yaz."
    )


@server.prompt(
    name="draft_metric_pack",
    description="Bir sektor/rol icin yeni Metric Pack taslagi hazirlar.",
)
def draft_metric_pack_prompt(entity_type: str, domain: str) -> str:
    return (
        f"'{domain}' alani icin '{entity_type}' tipinde bir Metric Pack taslagi hazirla.\n\n"
        "1. Once humetric_list_packs ve humetric_get_pack ile mevcut bir pack'i "
        "incele; YAML bicimini ve alan adlarini oradan al.\n"
        "2. 6-10 metrik oner. Her biri icin: key (snake_case), label, type, "
        "cikarim prompt'u, sensitive bayragi ve default_confidence.\n"
        "3. Kisisel/hassas veri iceren metrikleri sensitive: true isaretle ve "
        "kvkk bolumunde listele.\n\n"
        "YAML'i bana goster ve ONAY BEKLE. Onaylamadan humetric_create_pack "
        "cagirma."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="HuMetric MCP sunucusu")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio")
    parser.add_argument("--port", type=int, default=8765, help="sse/streamable-http icin port")
    args = parser.parse_args()

    if not _api_key():
        # Oldurucu degil: sunucu ayaga kalkar ve hatayi tool cagrisinda anlatir.
        # Yine de baslangicta uyarmak, sorunu Claude'a sormadan gormeyi saglar.
        _log.warning(
            "HUMETRIC_MCP_API_KEY tanimli degil — tool cagrilari kimlik dogrulama "
            "hatasi dondurecek."
        )
    _log.info("HuMetric MCP sunucusu baslatiliyor (transport=%s, api=%s)", args.transport, BASE_URL)

    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.settings.port = args.port
        server.run(transport=args.transport)


if __name__ == "__main__":
    main()
