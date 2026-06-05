from .client import HuMetricClient
from .logger import ScenarioLogger, StepResult


class ScenarioConfig:
    def __init__(self, name: str, label: str):
        self.name = name
        self.label = label
        self.onceki_basarili = True


class ScenarioReport:
    def __init__(self, config: ScenarioConfig, logger: ScenarioLogger):
        self.config = config
        self.logger = logger


class ScenarioRunner:
    def __init__(self, client: HuMetricClient, verbose: bool = False):
        self.client = client
        self.verbose = verbose

    def _print(self, msg: str) -> None:
        if self.verbose:
            print(f"  {msg}")

    def _check(self, result: StepResult, label: str) -> bool:
        if result.status == "passed":
            self._print(f"[PASS] {label}")
            return True
        elif result.status == "skipped":
            self._print(f"[SKIP] {label}: {result.message}")
            return False
        else:
            self._print(f"[FAIL] {label}: {result.message}")
            return False

    def run_register(self, email: str, password: str, logger: ScenarioLogger) -> dict:
        import uuid
        import os
        from itsdangerous import URLSafeTimedSerializer

        unique_part = uuid.uuid4().hex[:6]

        r = self.client.register(email, password)
        logger.add(r)
        self._check(r, "POST /v1/register")
        if r.status != "passed" or not r.response_body:
            return {}

        tenant_id = r.response_body.get("tenant_id")
        if not tenant_id:
            return {}

        from humetric.config import AUTH_SECRET
        serializer = URLSafeTimedSerializer(AUTH_SECRET)
        token = serializer.dumps(str(tenant_id))

        r2 = self.client.verify_email(token)
        logger.add(r2)
        self._check(r2, "GET /v1/verify-email")
        if r2.status != "passed" or not r2.response_body:
            return {}

        api_key = r2.response_body.get("api_key")
        if not api_key:
            return {}

        self.client.set_api_key(api_key)

        return {"tenant_id": tenant_id, "api_key": api_key, "email": email}

    def run_create_full_scope_key(self, logger: ScenarioLogger) -> str | None:
        label = "humetric-test-full-scope"
        scopes = ["signals:write", "entities:read", "entities:write",
                   "signals:read", "query", "packs:admin", "packs:read"]

        r = self.client.create_api_key("hm_test", label, scopes)
        logger.add(r)
        self._check(r, "POST /v1/api-keys (full-scope)")

        if r.status == "passed" and r.response_body:
            new_key = r.response_body.get("full_key") or r.response_body.get("api_key")
            if new_key:
                self.client.set_api_key(new_key)
                return new_key
        return None

    def run_pack_ops(self, pack_yaml: str, logger: ScenarioLogger) -> dict | None:
        import yaml
        pack_def = yaml.safe_load(pack_yaml)

        r = self.client.create_pack(pack_yaml)
        logger.add(r)
        self._check(r, f"POST /v1/packs - {pack_def.get('entity_type', '?')}")
        if r.status != "passed" or not r.response_body:
            return None
        return r.response_body

    def run_entity_ops(self, entity_defs: list[dict], logger: ScenarioLogger,
                       skip_ids: set | None = None) -> dict[str, dict]:
        created: dict[str, dict] = {}
        skip_set = skip_ids or set()

        for ent in entity_defs:
            eid = ent.get("id", "?")
            if eid in skip_set:
                sr = logger.add_skipped(f"POST /v1/entities - {eid}", "POST", "/v1/entities",
                                        f"Bagimli adim basarisiz")
                continue

            r = self.client.create_entity(ent)
            logger.add(r)
            if self._check(r, f"POST /v1/entities - {eid}"):
                if r.response_body:
                    created[eid] = r.response_body
            else:
                skip_set.add(eid)

        return created

    def run_signal_ops(self, signal_defs: list[dict], logger: ScenarioLogger,
                       skip_entity_ids: set | None = None,
                       interval: int = 2, timeout: int = 60) -> dict[str, dict]:
        results: dict[str, dict] = {}
        skip_set = skip_entity_ids or set()

        for sig in signal_defs:
            eid = sig.get("entity_id", "?")
            etype = sig.get("entity_type", "?")
            text = sig.get("text", "")

            if eid in skip_set:
                logger.add_skipped(f"POST /v1/signals - {eid}", "POST", "/v1/signals",
                                   f"Entity {eid} olusturulamadi")
                continue

            r = self.client.submit_signal(eid, etype, text)
            logger.add(r)
            if not self._check(r, f"POST /v1/signals - {eid}"):
                continue

            if not r.response_body:
                continue
            signal_id = r.response_body.get("signal_id")
            if not signal_id:
                continue

            poll_result = self._poll_signal(signal_id, logger, interval, timeout)
            results[eid] = {
                "signal_id": signal_id,
                "poll_result": poll_result,
            }

        return results

    def _poll_signal(self, signal_id: str, logger: ScenarioLogger,
                     interval: int = 2, timeout: int = 60) -> dict | None:
        import time
        elapsed = 0.0
        while elapsed < timeout:
            time.sleep(interval)
            elapsed += interval
            r = self.client.get_signal(signal_id)
            logger.add(r)
            if r.status != "passed" or not r.response_body:
                continue
            status = r.response_body.get("status", "")
            self._print(f"  Signal {signal_id}: {status} ({elapsed:.0f}s)")
            if status in ("completed", "failed"):
                return r.response_body
        return None

    def run_query_test(self, queries: list[dict], logger: ScenarioLogger) -> None:
        for q in queries:
            qtext = q.get("query", "")
            etype = q.get("entity_type")
            top_k = q.get("top_k", 10)
            filters = q.get("filters")
            r = self.client.query(qtext, etype, top_k, filters)
            logger.add(r)
            self._check(r, f"POST /v1/query - {qtext[:50]}...")

    def run_consent_ops(self, entity_id: str, scope: str, logger: ScenarioLogger,
                        grant: bool = True) -> bool:
        if grant:
            r = self.client.grant_consent(entity_id, scope)
            logger.add(r)
            return self._check(r, f"POST /v1/consent - {entity_id}")
        else:
            r = self.client.revoke_consent(entity_id, scope)
            logger.add(r)
            return self._check(r, f"DELETE /v1/consent - {entity_id}")
