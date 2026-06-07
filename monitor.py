"""HuMetric Monitoring CLI — Operasyonel izleme dashboard'u.

Kullanim:
    python monitor.py                    # Anlik durum
    python monitor.py --watch            # 5 saniyede bir yenile (Ctrl+C cikis)
    python monitor.py --entities isci    # Belirli entity tipine gore metrik dagilimi
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.error

BASE_URL = os.environ.get("HUMETRIC_BASE_URL", "http://localhost:8002")
API_KEY = os.environ.get("HUMETRIC_ADMIN_KEY", "")


def _req(path: str) -> dict:
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def _rpad(s: str, w: int) -> str:
    return s[:w].ljust(w)


def show_overview():
    """Genel durum goruntule."""
    health = _req("/healthz")
    worker = _req("/healthz/worker") if API_KEY else {}

    print("=" * 60)
    print("  HuMetric Monitor")
    print("=" * 60)

    svc = health.get("service", "?")
    ver = health.get("version", "?")
    print(f"\n  Service:  {svc} v{ver}")

    if worker and "error" not in worker:
        qd = worker.get("queue_depth", "?")
        old = worker.get("oldest_pending_seconds", 0)
        fail = worker.get("failed_last_hour", "?")
        print(f"  Queue:    {qd} pending")
        print(f"  Oldest:   {old}s")
        print(f"  Failed:   {fail} (last hour)")
    elif not API_KEY:
        print("  (set HUMETRIC_ADMIN_KEY for worker stats)")
    else:
        print(f"  Worker:   error - {worker.get('error', worker)}")


def show_entities(entity_type: str | None = None):
    """Entity ve metrik dagilimi."""
    if not API_KEY:
        print("\n  (set HUMETRIC_ADMIN_KEY for entity stats)")
        return

    body = {"top_k": 50, "include_reasoning": False}
    if entity_type:
        body["entity_type"] = entity_type

    url = f"{BASE_URL}/v1/query"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"\n  Query error: {e}")
        return

    results = data.get("results", [])
    print(f"\n  Entities ({entity_type or 'all'}): {len(results)}")
    for r in results[:10]:
        eid = r.get("entity_id", r.get("id", "?"))
        etype = r.get("entity_type", "?")
        score = r.get("score", 0)
        metrics = r.get("metrics", [])
        m_str = ", ".join(
            f"{m.get('metric_key', '?')}={m.get('value', 0):+.2f}"
            for m in metrics[:3]
        ) or "no metrics"
        print(f"    {_rpad(eid, 20)} [{_rpad(etype, 8)}] score={score:.2f}  {m_str}")


def show_usage():
    """Kullanim istatistikleri (tenant dashboard)."""
    if not API_KEY:
        return
    dash = _req("/v1/tenant/dashboard")
    if "error" in dash:
        return
    usage = dash.get("usage", {})
    tier = dash.get("tier", "?")
    print(f"\n  Usage (tier: {tier}):")
    print(f"    Signals this month:  {usage.get('sinyal_sayisi', 0)}")
    print(f"    LLM tokens:          {usage.get('llm_token_sayisi', 0)}")
    print(f"    Embeddings:          {usage.get('embedding_sayisi', 0)}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="HuMetric Monitor")
    parser.add_argument("--watch", action="store_true", help="Continuous monitoring")
    parser.add_argument("--entities", type=str, default=None, help="Filter entity type")
    args = parser.parse_args()

    if args.watch:
        try:
            while True:
                os.system("cls" if os.name == "nt" else "clear")
                show_overview()
                show_usage()
                show_entities(args.entities)
                print(f"\n  [Updating every 5s — {time.strftime('%H:%M:%S')}]")
                time.sleep(5)
        except KeyboardInterrupt:
            print("\n  Stopped.")
    else:
        show_overview()
        show_usage()
        show_entities(args.entities)


if __name__ == "__main__":
    main()
