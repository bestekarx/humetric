"""Beta smoke test — end-to-end "real user journey" against a live HuMetric API.

Runs the full path a new tenant walks: register → login → create API key →
pack wizard (LLM) → create pack → entities → signals → poll → trace/metrics →
query → usage metering → BYOK beta-lock regression.

Designed to run both locally (``--base-url http://localhost:8002/v1``) and against
production (``HUMETRIC_TEST_BASE_URL=https://api.gethumetric.com/v1``). Tenants it
creates use a ``beta-smoke-*`` email so they are trivial to spot and clean up.
"""

import uuid
from pathlib import Path

from ..client import HuMetricClient
from ..logger import ScenarioLogger
from ..runner import ScenarioRunner

PACK_YAML_PATH = Path(__file__).resolve().parents[2] / "packs" / "lastik-bayi.yaml"

STRONG_PASSWORD = "BetaSmoke!2026xZ"


def _forge_verify_token(tenant_id: int) -> str | None:
    """Locally forge an email-verification token (needs AUTH_SECRET on this host).

    Only works when the smoke test runs on the same host as the API (local dev).
    In production REQUIRE_EMAIL_VERIFICATION=false, so the tenant is auto-verified
    at register time and this path is never needed.
    """
    try:
        from itsdangerous import URLSafeTimedSerializer

        from humetric.config import AUTH_SECRET

        if not AUTH_SECRET:
            return None
        return URLSafeTimedSerializer(AUTH_SECRET).dumps(str(tenant_id))
    except Exception:
        return None


def build_beta_smoke_scenario(runner: ScenarioRunner, client: HuMetricClient,
                              logger: ScenarioLogger) -> None:
    email = f"beta-smoke-{uuid.uuid4().hex[:8]}@humetric.local"

    # ── 1. Register ────────────────────────────────────────────────────────
    r = client.register(email, STRONG_PASSWORD, captcha_token="test")
    logger.add(r)
    if not runner._check(r, "POST /v1/register") or not r.response_body:
        logger.add_failed("beta_smoke:register", "", "", message="Register failed; aborting")
        return
    tenant_id = r.response_body.get("tenant_id")
    email_verified = bool(r.response_body.get("email_verified"))
    runner._print(f"Tenant {tenant_id} registered (verified={email_verified})")

    # ── 1b. Verify (local only; prod auto-verifies) ─────────────────────────
    if not email_verified:
        token = _forge_verify_token(tenant_id)
        if not token:
            logger.add_failed(
                "beta_smoke:verify", "", "",
                message="Email not auto-verified and AUTH_SECRET unavailable to forge token",
            )
            return
        rv = client.verify_email(token)
        logger.add(rv)
        if not runner._check(rv, "GET /v1/verify-email"):
            return

    # ── 2. Login → dashboard token ──────────────────────────────────────────
    rl = client.login(email, STRONG_PASSWORD)
    logger.add(rl)
    if not runner._check(rl, "POST /v1/login") or not rl.response_body:
        logger.add_failed("beta_smoke:login", "", "", message="Login failed; aborting")
        return
    dashboard_token = rl.response_body.get("dashboard_token")
    if not dashboard_token:
        logger.add_failed("beta_smoke:login", "", "", message="No dashboard_token in login response")
        return
    client.set_api_key(dashboard_token)

    # ── 3. Create first real API key (with dashboard token) ─────────────────
    scopes = ["signals:write", "signals:read", "entities:read", "entities:write",
              "query", "packs:admin", "packs:read"]
    rk = client.create_api_key("hm_test", "beta-smoke", scopes)
    logger.add(rk)
    if not runner._check(rk, "POST /v1/api-keys") or not rk.response_body:
        logger.add_failed("beta_smoke:api-key", "", "", message="API key creation failed; aborting")
        return
    api_key = rk.response_body.get("full_key")
    if not api_key:
        logger.add_failed("beta_smoke:api-key", "", "", message="No full_key in response")
        return
    client.set_api_key(api_key)

    # ── 4. Pack wizard (LLM smoke) ──────────────────────────────────────────
    wizard_desc = (
        "Lastik dagitim bayilerini degerlendiren bir sistem. Her bayi icin satis "
        "performansi, tahsilat disiplini ve musteri memnuniyeti gibi metrikleri "
        "sinyal metinlerinden cikar."
    )
    rw = client.create_pack_wizard(wizard_desc)
    logger.add(rw)
    if runner._check(rw, "POST /v1/packs/wizard") and rw.response_body:
        pack_yaml = rw.response_body.get("pack_yaml") or ""
        v_errors = rw.response_body.get("validation_errors") or []
        if pack_yaml.strip() and not v_errors:
            logger.add_passed("beta_smoke:wizard-valid", "", "",
                              message=f"wizard produced valid pack ({len(pack_yaml)} chars)")
        else:
            logger.add_failed("beta_smoke:wizard-valid", "", "",
                              message=f"wizard validation_errors={v_errors}")

    # ── 5. Create pack (deterministic lastik-bayi for a stable journey) ─────
    pack_yaml = PACK_YAML_PATH.read_text(encoding="utf-8")
    pack_result = runner.run_pack_ops(pack_yaml, logger)
    if not pack_result:
        logger.add_failed("beta_smoke:pack", "", "", message="Pack creation failed; aborting")
        return

    # ── 6. Entities ─────────────────────────────────────────────────────────
    entities = [
        {
            "id": "smoke_bayi_ist",
            "entity_type": "bayi",
            "fields": {"bolge": "Istanbul", "satis_adedi": 1200},
            "free_text": "Istanbul Anadolu yakasinda faaliyet gosteren lastik dagitim bayisi.",
        },
        {
            "id": "smoke_bayi_ank",
            "entity_type": "bayi",
            "fields": {"bolge": "Ankara", "satis_adedi": 850},
            "free_text": "Ankara merkezde agir vasita lastiklerinde uzmanlasmis bayi.",
        },
    ]
    created = runner.run_entity_ops(entities, logger)
    if len(created) < 2:
        logger.add_failed("beta_smoke:entities", "", "",
                          message=f"Expected 2 entities, created {len(created)}")

    # ── 7 + 8. Signals (2 per entity) + poll to completion ──────────────────
    signals = [
        {"entity_id": "smoke_bayi_ist", "entity_type": "bayi",
         "text": "Bayi bu ay %15 buyume gosterdi. Satis hedefinin %110'u yakalandi. "
                 "Musteri sikayeti yok, tahsilatlar zamaninda yapildi."},
        {"entity_id": "smoke_bayi_ist", "entity_type": "bayi",
         "text": "Istanbul bayisi son ceyrekte pazar payini %5 artirdi. Musteri "
                 "memnuniyeti anketinde 4.7/5 puan aldi."},
        {"entity_id": "smoke_bayi_ank", "entity_type": "bayi",
         "text": "Ankara bayisi agir vasita segmentinde lider. Tahsilat performansi "
                 "sektor ortalamasinin uzerinde. 2 gecikmis odeme cozuldu."},
        {"entity_id": "smoke_bayi_ank", "entity_type": "bayi",
         "text": "Ankara bayisi yeni filo anlasmasi imzaladi. Aylik satis hacmi %20 "
                 "artti. Musterilerden olumlu geri donusler aliniyor."},
    ]
    signal_results = runner.run_signal_ops(signals, logger)

    completed_ids: list[str] = []
    for eid, info in signal_results.items():
        poll = info.get("poll_result")
        sid = info.get("signal_id")
        status = (poll or {}).get("status")
        if status == "completed":
            completed_ids.append(sid)
            logger.add_passed(f"beta_smoke:signal-completed:{eid}", "", "",
                              message=f"signal {sid} completed")
        else:
            err = (poll or {}).get("error")
            logger.add_failed(f"beta_smoke:signal-completed:{eid}", "", "",
                              message=f"signal {sid} status={status} error={err}")

    # ── 9. Trace verification ───────────────────────────────────────────────
    # The signal trace exposes the persisted result: entity_metrics proves the
    # extract→curate pipeline ran and produced metrics for this entity.
    if completed_ids:
        rt = client.get_signal_trace(completed_ids[0])
        logger.add(rt)
        if runner._check(rt, "GET /v1/signals/{id}/trace") and rt.response_body:
            trace = rt.response_body.get("trace_data") or {}
            entity_metrics = trace.get("entity_metrics") or []
            if entity_metrics:
                logger.add_passed("beta_smoke:trace", "", "",
                                  message=f"trace has {len(entity_metrics)} entity_metrics")
            else:
                logger.add_failed("beta_smoke:trace", "", "",
                                  message="trace_data.entity_metrics empty")

    # ── 10. Metric verification ─────────────────────────────────────────────
    rm = client.get_entity_metrics("smoke_bayi_ist")
    logger.add(rm)
    if runner._check(rm, "GET /v1/entities/{id}/metrics") and rm.response_body:
        metrics = rm.response_body.get("metrics") or []
        count = rm.response_body.get("metric_count", len(metrics))
        in_range = all(-1.0 <= (m.get("value") or 0) <= 1.0 for m in metrics)
        if count > 0 and in_range:
            logger.add_passed("beta_smoke:metrics", "", "",
                              message=f"metric_count={count}, all values in [-1,1]")
        else:
            logger.add_failed("beta_smoke:metrics", "", "",
                              message=f"metric_count={count}, in_range={in_range}")

    # ── 11. Query ───────────────────────────────────────────────────────────
    rq = client.query_free_text(
        "satis performansi ve tahsilati guclu bayi",
        entity_type="bayi", top_k=5, include_reasoning=True,
    )
    logger.add(rq)
    if runner._check(rq, "POST /v1/query") and rq.response_body:
        results = rq.response_body.get("results") or []
        ids = {r.get("entity_id") for r in results}
        if results and ids & {"smoke_bayi_ist", "smoke_bayi_ank"}:
            logger.add_passed("beta_smoke:query", "", "",
                              message=f"query returned {len(results)} results incl. created entities")
        else:
            logger.add_failed("beta_smoke:query", "", "",
                              message=f"query returned {len(results)} results, ids={ids}")

    # ── 12. Usage metering ──────────────────────────────────────────────────
    rd = client.tenant_dashboard()
    logger.add(rd)
    if runner._check(rd, "GET /v1/tenant/dashboard") and rd.response_body:
        usage = rd.response_body.get("usage_current_month") or {}
        sc = usage.get("signal_count", 0)
        tc = usage.get("llm_token_count", 0)
        if sc >= 4 and tc > 0:
            logger.add_passed("beta_smoke:usage", "", "",
                              message=f"signal_count={sc}, llm_token_count={tc}")
        else:
            logger.add_failed("beta_smoke:usage", "", "",
                              message=f"signal_count={sc} (want >=4), llm_token_count={tc} (want >0)")

    # ── 13. BYOK beta-lock regression ───────────────────────────────────────
    r422 = client.put_tenant_keys(
        {"openai_key": "sk-fake", "llm_provider": "openai"}, expected_status=422,
    )
    logger.add(r422)
    runner._check(r422, "PUT /v1/tenant/keys llm_provider=openai → 422")

    rdel = client.delete_tenant_keys(expected_status=200)
    logger.add(rdel)
    if runner._check(rdel, "DELETE /v1/tenant/keys") and rdel.response_body:
        prov = rdel.response_body.get("llm_provider")
        has_any = any(rdel.response_body.get(k) for k in
                      ("has_anthropic_key", "has_openai_key", "has_google_ai_key",
                       "has_deepseek_key", "has_voyage_key"))
        if prov == "anthropic" and not has_any:
            logger.add_passed("beta_smoke:byok-reset", "", "",
                              message="provider reset to anthropic, all keys cleared")
        else:
            logger.add_failed("beta_smoke:byok-reset", "", "",
                              message=f"llm_provider={prov}, has_any={has_any}")
