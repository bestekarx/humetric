#!/usr/bin/env python3
"""HuMetric MCP Server — Claude Desktop entegrasyonu (Spec 026).

Transport: stdio (Claude Desktop) veya SSE (remote host).
4 MCP tool: humetric_ingest_signal, humetric_query_entities,
            humetric_get_entity, humetric_list_entities.

Kullanim:
  python mcp_server.py --transport stdio
  python mcp_server.py --transport sse --port 8765

Config: .env veya env var:
  HUMETRIC_MCP_API_KEY
  HUMETRIC_BASE_URL (default: http://localhost:8002)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any

import httpx
from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

load_dotenv()

_log = logging.getLogger("humetric.mcp")

API_KEY = os.environ.get("HUMETRIC_MCP_API_KEY", "")
BASE_URL = os.environ.get("HUMETRIC_BASE_URL", "http://localhost:8002").rstrip("/")

if not API_KEY:
    _log.critical("HUMETRIC_MCP_API_KEY not set. MCP server cannot authenticate.")
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

server = Server("humetric")


async def _api_request(method: str, path: str, json_data: dict | None = None) -> dict[str, Any]:
    url = f"{BASE_URL}{path}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        if method == "GET":
            resp = await client.get(url, headers=HEADERS)
        elif method == "POST":
            resp = await client.post(url, headers=HEADERS, json=json_data or {})
        else:
            return {"error": f"Unsupported method: {method}"}

        if resp.status_code == 401:
            return {"error": "API key invalid. Check HUMETRIC_MCP_API_KEY."}
        if resp.status_code == 429:
            return {"error": "Rate limit exceeded. Try again later."}
        if resp.status_code >= 500:
            return {"error": f"API unavailable (HTTP {resp.status_code})."}

        try:
            return resp.json()
        except Exception:
            return {"error": f"Invalid response from API (HTTP {resp.status_code})."}


TOOL_DEFINITIONS = [
    Tool(
        name="humetric_ingest_signal",
        description="Bir entity hakkinda gozlem/sinyal gonderir.",
        inputSchema={
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Hedef entity ID"},
                "entity_type": {"type": "string", "description": "Entity tipi (ornegin: satici, musteri, urun)"},
                "text": {"type": "string", "description": "Dogal dil gozlem metni"},
                "structured": {
                    "type": "object",
                    "description": "Opsiyonel yapisal veri (key-value)",
                },
            },
            "required": ["entity_id", "entity_type", "text"],
        },
    ),
    Tool(
        name="humetric_query_entities",
        description="Metrik bazli entity sorgulama ve siralama.",
        inputSchema={
            "type": "object",
            "properties": {
                "entity_type": {"type": "string", "description": "Sorgulanacak entity tipi"},
                "rank_by": {"type": "string", "description": "Siralama metrigi"},
                "filters": {
                    "type": "object",
                    "description": "Opsiyonel filtreler (key-value)",
                },
                "free_text_query": {
                    "type": "string",
                    "description": "Serbest metin aramasi",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Donulecek maksimum sonuc (default: 10)",
                },
            },
            "required": ["entity_type", "rank_by"],
        },
    ),
    Tool(
        name="humetric_get_entity",
        description="Tek entity detayi ve tum metriklerini getirir.",
        inputSchema={
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Entity ID"},
            },
            "required": ["entity_id"],
        },
    ),
    Tool(
        name="humetric_list_entities",
        description="Sayfali entity listesi.",
        inputSchema={
            "type": "object",
            "properties": {
                "entity_type": {
                    "type": "string",
                    "description": "Filtre (yoksa tum tipler)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Sayfa boyutu (default: 20, max: 100)",
                },
                "offset": {
                    "type": "integer",
                    "description": "Atlanacak kayit sayisi (sayfalama icin)",
                },
            },
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOL_DEFINITIONS


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        if name == "humetric_ingest_signal":
            result = await _api_request("POST", "/v1/signals", {
                "entity_id": arguments["entity_id"],
                "entity_type": arguments.get("entity_type", ""),
                "text": arguments.get("text", ""),
                "structured": arguments.get("structured"),
            })
            return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

        elif name == "humetric_query_entities":
            result = await _api_request("POST", "/v1/query", {
                "entity_type": arguments["entity_type"],
                "rank_by": arguments["rank_by"],
                "filters": arguments.get("filters"),
                "free_text_query": arguments.get("free_text_query"),
                "top_k": arguments.get("top_k", 10),
            })
            return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

        elif name == "humetric_get_entity":
            entity_id = arguments["entity_id"]
            result = await _api_request("GET", f"/v1/entities/{entity_id}")
            return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

        elif name == "humetric_list_entities":
            params = []
            if arguments.get("entity_type"):
                params.append(f"entity_type={arguments['entity_type']}")
            if arguments.get("limit"):
                params.append(f"limit={arguments['limit']}")
            if arguments.get("offset"):
                params.append(f"offset={arguments['offset']}")
            qs = "&".join(params)
            path = f"/v1/entities?{qs}" if qs else "/v1/entities"
            result = await _api_request("GET", path)
            return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as e:
        _log.exception("Tool error: %s", name)
        return [TextContent(type="text", text=json.dumps({"error": str(e)}, indent=2))]


async def run_stdio():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


async def run_sse(port: int):
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.routing import Route
    import uvicorn

    sse = SseServerTransport("/messages")

    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await server.run(
                streams[0],
                streams[1],
                server.create_initialization_options(),
            )

    async def handle_messages(request):
        await sse.handle_post_message(
            request.scope, request.receive, request._send
        )

    starlette_app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Route("/messages", endpoint=handle_messages, methods=["POST"]),
        ]
    )
    config = uvicorn.Config(starlette_app, host="0.0.0.0", port=port, log_level="info")
    server_instance = uvicorn.Server(config)
    await server_instance.serve()


def main():
    parser = argparse.ArgumentParser(description="HuMetric MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    import asyncio

    if args.transport == "sse":
        _log.info("Starting MCP SSE server on port %d", args.port)
        asyncio.run(run_sse(args.port))
    else:
        _log.info("Starting MCP stdio server")
        asyncio.run(run_stdio())


if __name__ == "__main__":
    main()
