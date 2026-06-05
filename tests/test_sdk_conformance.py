"""Cross-SDK conformance test — validates OpenAPI spec completeness for SDK generation (Spec 025)."""

import pytest
from fastapi.testclient import TestClient
from humetric.api import app

client = TestClient(app)


class TestOpenAPICompleteness:
    """Validate that OpenAPI spec contains everything SDK generators need."""

    def test_openapi_json_valid(self):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        spec = resp.json()
        assert "openapi" in spec
        assert spec["openapi"].startswith("3.")
        assert "paths" in spec
        assert "components" in spec

    def test_auth_schema_present(self):
        resp = client.get("/openapi.json")
        spec = resp.json()
        security = spec.get("security", [])
        assert len(security) > 0, "No global security defined"
        schemes = spec.get("components", {}).get("securitySchemes", {})
        assert "bearerAuth" in schemes
        assert schemes["bearerAuth"]["scheme"] == "bearer"

    def test_all_v1_endpoints_documented(self):
        resp = client.get("/openapi.json")
        spec = resp.json()
        paths = spec.get("paths", {})

        required_paths = [
            "/v1/entities",
            "/v1/entities/{entity_id}",
            "/v1/entities/{entity_id}/metrics",
            "/v1/signals",
            "/v1/signals/{signal_id}",
            "/v1/signals/{signal_id}/trace",
            "/v1/query",
            "/v1/packs",
            "/v1/packs/{pack_key}",
            "/v1/packs/wizard",
            "/v1/api-keys",
            "/v1/api-keys/{key_id}",
            "/v1/consent",
            "/v1/consent/{entity_id}",
            "/v1/tenant/keys",
        ]

        for path in required_paths:
            assert path in paths, f"Missing: {path}"

    def test_error_schema_documented(self):
        resp = client.get("/openapi.json")
        spec = resp.json()
        schemas = spec.get("components", {}).get("schemas", {})
        assert "ErrorResponse" in schemas
        assert "ErrorDetail" in schemas

    def test_endpoints_have_tags(self):
        resp = client.get("/openapi.json")
        spec = resp.json()
        paths = spec.get("paths", {})

        untagged = []
        for path, methods in paths.items():
            if path.startswith("/v1/"):
                for method, details in methods.items():
                    if method in ("get", "post", "put", "delete", "patch"):
                        if "tags" not in details:
                            untagged.append(f"{method.upper()} {path}")

        assert len(untagged) == 0, f"Untagged V1 endpoints: {untagged}"

    def test_swagger_ui_accessible(self):
        resp = client.get("/docs")
        assert resp.status_code == 200
        assert "swagger" in resp.text.lower() or "openapi" in resp.text.lower()
