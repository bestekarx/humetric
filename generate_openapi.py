"""HuMetric OpenAPI spec uretici.

Calistirma: cd humetric && python generate_openapi.py
Cikti: humetric/openapi.json (static OpenAPI 3.1)
"""
from __future__ import annotations

import json
import os
import sys

# HuMetric src dizinini Python path'ine ekle
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from humetric.api import app
from humetric import config as hm_config


def main():
    # Tembel import'lari tetikle
    spec = app.openapi()
    
    # info'yu guclendir
    spec["info"]["description"] = (
        "HuMetric — Domain-agnostik, cok-sektorlu metrik motoru.\n\n"
        "Saha hizmet calisanlari, lastik bayileri, ilac mümessilleri ve daha fazlasi "
        "icin AI-gudumlu performans metrik cikarimi, kure etme ve hibrit arama.\n\n"
        "## Quickstart\n"
        "1. Register: `POST /v1/register`\n"
        "2. Verify email: `GET /v1/verify-email?token=...`\n"
        "3. Create pack: `POST /v1/packs`\n"
        "4. Create entity: `POST /v1/entities`\n"
        "5. Send signal: `POST /v1/signals`\n"
        "6. Query: `POST /v1/query`\n\n"
        "## Auth\n"
        "Bearer API key: `Authorization: Bearer hm_live_...`\n"
        "Scopes: `signals:write`, `entities:read`, `entities:write`, "
        "`signals:read`, `query`, `packs:read`, `packs:admin`\n\n"
        "## Rate Limiting\n"
        f"Default: {hm_config.HUMETRIC_RATE_LIMIT} requests/min per API key\n"
        "Headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`, `Retry-After`"
    )
    
    spec["info"]["version"] = "0.1.0"
    spec["info"]["contact"] = {
        "name": "HuMetric API Support",
        "url": "https://github.com/bestekarx/humetric",
    }
    
    spec["externalDocs"] = {
        "description": "Full Documentation",
        "url": "https://docs.humetric.dev",
    }
    
    out_path = os.path.join(os.path.dirname(__file__), "openapi.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=2, ensure_ascii=False)
    
    print(f"OpenAPI spec written: {out_path}")
    print(f"Endpoints: {len(spec.get('paths', {}))}")
    print(f"Schemas: {len(spec.get('components', {}).get('schemas', {}))}")


if __name__ == "__main__":
    main()
