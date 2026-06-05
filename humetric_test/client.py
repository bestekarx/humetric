import time
from typing import Any

import httpx

from .logger import StepResult


class HuMetricClient:
    def __init__(self, base_url: str = "http://localhost:8002/v1"):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=httpx.Timeout(30.0))
        self.api_key: str | None = None

    def close(self) -> None:
        self._client.close()

    def set_api_key(self, key: str) -> None:
        self.api_key = key

    def _headers(self, include_auth: bool = True) -> dict:
        h = {"Content-Type": "application/json"}
        if include_auth and self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _request(
        self,
        method: str,
        path: str,
        step_name: str,
        body: dict | None = None,
        params: dict | None = None,
        auth: bool = True,
        expected_status: int | None = None,
    ) -> StepResult:
        url = f"{self.base_url}{path}"
        t0 = time.time()
        try:
            if method == "GET":
                resp = self._client.get(url, headers=self._headers(auth), params=params)
            elif method == "POST":
                resp = self._client.post(url, headers=self._headers(auth), json=body)
            elif method == "PUT":
                resp = self._client.put(url, headers=self._headers(auth), json=body)
            elif method == "DELETE":
                resp = self._client.delete(url, headers=self._headers(auth), params=params)
            else:
                raise ValueError(f"Unknown method: {method}")
            elapsed = int((time.time() - t0) * 1000)
        except httpx.ConnectError as e:
            elapsed = int((time.time() - t0) * 1000)
            return StepResult(step_name, "failed", method, url, body, 0, None, elapsed,
                              f"Connection error: {e}")
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000)
            return StepResult(step_name, "failed", method, url, body, 0, None, elapsed,
                              f"Exception: {e}")

        resp_body: dict | None = None
        try:
            resp_body = resp.json()
        except Exception:
            resp_body = {"_raw": resp.text}

        if expected_status is not None and resp.status_code != expected_status:
            return StepResult(step_name, "failed", method, url, body, resp.status_code, resp_body, elapsed,
                              f"Expected {expected_status}, got {resp.status_code}")

        return StepResult(step_name, "passed", method, url, body, resp.status_code, resp_body, elapsed, "")

    # --- Tenant & Auth ---

    def register(self, email: str, password: str, captcha_token: str = "test") -> StepResult:
        return self._request("POST", "/register", "POST /v1/register",
                             body={"email": email, "password": password, "captcha_token": captcha_token},
                             auth=False, expected_status=201)

    def verify_email(self, token: str) -> StepResult:
        return self._request("GET", "/verify-email", "GET /v1/verify-email",
                             params={"token": token}, auth=False, expected_status=200)

    # --- API Keys ---

    def create_api_key(self, prefix: str, label: str, scopes: list[str]) -> StepResult:
        return self._request("POST", "/api-keys", "POST /v1/api-keys",
                             body={"prefix": prefix, "label": label, "scopes": scopes},
                             expected_status=201)

    def list_api_keys(self) -> StepResult:
        return self._request("GET", "/api-keys", "GET /v1/api-keys", expected_status=200)

    def revoke_api_key(self, key_id: int) -> StepResult:
        return self._request("DELETE", f"/api-keys/{key_id}", "DELETE /v1/api-keys/{key_id}",
                             expected_status=200)

    # --- Packs ---

    def create_pack(self, pack_yaml: str) -> StepResult:
        return self._request("POST", "/packs", "POST /v1/packs", body={"yaml_text": pack_yaml}, expected_status=201)

    def list_packs(self, is_active: bool = True) -> StepResult:
        return self._request("GET", "/packs", "GET /v1/packs",
                             params={"is_active": str(is_active).lower()}, expected_status=200)

    def get_pack(self, pack_key: str) -> StepResult:
        return self._request("GET", f"/packs/{pack_key}", f"GET /v1/packs/{pack_key}", expected_status=200)

    def update_pack(self, pack_key: str, pack_def: dict) -> StepResult:
        return self._request("PUT", f"/packs/{pack_key}", f"PUT /v1/packs/{pack_key}",
                             body=pack_def, expected_status=200)

    def create_pack_wizard(self, domain_description: str) -> StepResult:
        return self._request("POST", "/packs/wizard", "POST /v1/packs/wizard",
                             body={"text": domain_description}, expected_status=200)

    # --- Entities ---

    def create_entity(self, entity_def: dict) -> StepResult:
        return self._request("POST", "/entities", "POST /v1/entities",
                             body=entity_def, expected_status=201)

    def get_entity(self, entity_id: str) -> StepResult:
        return self._request("GET", f"/entities/{entity_id}", f"GET /v1/entities/{entity_id}",
                             expected_status=200)

    def get_entity_metrics(self, entity_id: str) -> StepResult:
        return self._request("GET", f"/entities/{entity_id}/metrics",
                             f"GET /v1/entities/{entity_id}/metrics", expected_status=200)

    # --- Signals ---

    def submit_signal(self, entity_id: str, entity_type: str, text: str) -> StepResult:
        return self._request("POST", "/signals", "POST /v1/signals",
                             body={"entity_id": entity_id, "entity_type": entity_type, "text": text},
                             expected_status=202)

    def get_signal(self, signal_id: str) -> StepResult:
        return self._request("GET", f"/signals/{signal_id}", f"GET /v1/signals/{signal_id}",
                             expected_status=200)

    def get_signal_trace(self, signal_id: str) -> StepResult:
        return self._request("GET", f"/signals/{signal_id}/trace",
                             f"GET /v1/signals/{signal_id}/trace", expected_status=200)

    # --- Consent ---

    def grant_consent(self, entity_id: str, scope: str, expires_at: str | None = None) -> StepResult:
        body: dict[str, Any] = {"entity_id": entity_id, "scope": scope}
        if expires_at:
            body["expires_at"] = expires_at
        return self._request("POST", "/consent", "POST /v1/consent",
                             body=body, expected_status=201)

    def get_consent(self, entity_id: str) -> StepResult:
        return self._request("GET", f"/consent/{entity_id}", f"GET /v1/consent/{entity_id}",
                             expected_status=200)

    def revoke_consent(self, entity_id: str, scope: str) -> StepResult:
        return self._request("DELETE", f"/consent/{entity_id}",
                             "DELETE /v1/consent/{entity_id}",
                             params={"scope": scope}, expected_status=200)

    # --- Query ---

    def query(self, query: str, entity_type: str | None = None,
              top_k: int = 10, filters: dict | None = None) -> StepResult:
        body: dict[str, Any] = {"query": query, "top_k": top_k}
        if entity_type:
            body["entity_type"] = entity_type
        if filters:
            body["filters"] = filters
        return self._request("POST", "/query", "POST /v1/query",
                             body=body, expected_status=200)

    # --- Tenant ---

    def tenant_keys(self) -> StepResult:
        return self._request("GET", "/tenant/keys", "GET /v1/tenant/keys", expected_status=200)

    def tenant_dashboard(self) -> StepResult:
        return self._request("GET", "/tenant/dashboard", "GET /v1/tenant/dashboard",
                             expected_status=200)
