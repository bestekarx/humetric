from ..client import HuMetricClient
from ..logger import ScenarioLogger
from ..runner import ScenarioRunner

LASTIK_BAYI_YAML = """entity_type: test_edge
label: "Edge Case Test"
version: 1
required_fields:
  - key: ad
    type: str
    label: "Ad"
metrics:
  - key: puan
    label: "Puan"
    type: float
    prompt: "Genel performans puani"
    default_confidence: 0.5
prompts:
  extraction: |
    Verilen sinyal metninden puan metrigini cikar.
  curation: |
    Cikarilan metrikleri dogrula.
kvkk:
  sensitive_metrics: []
"""


def build_edge_cases_scenario(runner: ScenarioRunner, client: HuMetricClient,
                               logger: ScenarioLogger) -> None:
    gecersiz_yaml = "entity_type: \n  - broken: [\n: yaml"

    r = client._request("POST", "/packs", "POST /v1/packs - gecersiz YAML",
                        body={"yaml_text": gecersiz_yaml})
    logger.add(r)
    if r.response_status in (400, 422):
        logger.add_passed("Edge: gecersiz pack YAML -> hata", "POST", "/v1/packs",
                          response_status=r.response_status, response_body=r.response_body,
                          message=f"Hata kodu: {r.response_status}")
    else:
        logger.add_failed("Edge: gecersiz pack YAML -> hata", "POST", "/v1/packs",
                          response_status=r.response_status, response_body=r.response_body,
                          message=f"Beklenen 400/422, alinan: {r.response_status}")

    r = client._request("POST", "/entities", "Edge: pack yok -> entity (ham)",
                        body={
                            "id": "test_no_pack",
                            "entity_type": "nonexistent_type",
                            "fields": {"ad": "test"},
                            "free_text": "test",
                        })
    if r.response_status in (400, 404, 422):
        logger.add_passed("Edge: pack yok -> entity hata", "POST", "/v1/entities",
                          response_status=r.response_status, response_body=r.response_body,
                          message=f"Hata kodu: {r.response_status}")
    else:
        logger.add_failed("Edge: pack yok -> entity hata", "POST", "/v1/entities",
                          response_status=r.response_status, response_body=r.response_body,
                          message=f"Beklenen hata, alinan: {r.response_status}")

    r = client._request("POST", "/signals", "Edge: entity yok -> sinyal (ham)",
                        body={"entity_id": "nonexistent_entity", "entity_type": "test_edge",
                              "text": "test sinyal metni"})
    if r.response_status in (400, 404):
        logger.add_passed("Edge: entity yok -> sinyal hata", "POST", "/v1/signals",
                          response_status=r.response_status, response_body=r.response_body,
                          message=f"Hata kodu: {r.response_status}")
    else:
        logger.add_failed("Edge: entity yok -> sinyal hata", "POST", "/v1/signals",
                          response_status=r.response_status, response_body=r.response_body,
                          message=f"Beklenen hata, alinan: {r.response_status}")

    original_key = client.api_key
    client.set_api_key("hm_test_invalid_key_12345")
    r = client._request("GET", "/api-keys", "Edge: gecersiz API key (ham)")
    client.set_api_key(original_key)
    if r.response_status in (401, 403):
        logger.add_passed("Edge: gecersiz API key -> 401", "GET", "/v1/api-keys",
                          response_status=r.response_status, response_body=r.response_body,
                          message=f"Hata kodu: {r.response_status}")
    else:
        logger.add_failed("Edge: gecersiz API key -> 401", "GET", "/v1/api-keys",
                          response_status=r.response_status, response_body=r.response_body,
                          message=f"Beklenen 401, alinan: {r.response_status}")

    r = _test_duplicate_pack(client, logger)

    r = _test_missing_required_fields(client, logger)

    r = _test_api_key_revoke_flow(runner, client, logger)


def _test_duplicate_pack(client, logger):
    r = client.create_pack(LASTIK_BAYI_YAML)
    logger.add(r)

    r2 = client._request("POST", "/packs", "Edge: ayni pack tekrar (ham)",
                         body={"yaml_text": LASTIK_BAYI_YAML})
    if r2.response_status == 409:
        logger.add_passed("Edge: ayni pack tekrar -> 409", "POST", "/v1/packs",
                          response_status=409, message="Duplicate pack dogru sekilde reddedildi")
    else:
        logger.add_failed("Edge: ayni pack tekrar -> 409", "POST", "/v1/packs",
                          response_status=r2.response_status, response_body=r2.response_body,
                          message=f"Beklenen 409, alinan: {r2.response_status}")
    return r2


def _test_missing_required_fields(client, logger):
    r = client._request("POST", "/entities", "Edge: eksik required_fields (ham)",
                        body={
                            "id": "test_missing_fields",
                            "entity_type": "test_edge",
                            "fields": {},
                            "free_text": "eksik zorunlu alan",
                        })
    if r.response_status in (400, 422):
        logger.add_passed("Edge: eksik required_fields -> hata", "POST", "/v1/entities",
                          response_status=r.response_status, response_body=r.response_body,
                          message=f"Hata kodu: {r.response_status}")
    else:
        logger.add_failed("Edge: eksik required_fields -> hata", "POST", "/v1/entities",
                          response_status=r.response_status, response_body=r.response_body,
                          message=f"Beklenen hata, alinan: {r.response_status}")
    return r


def _test_api_key_revoke_flow(runner, client, logger):
    r = client.list_api_keys()
    logger.add(r)
    if r.status != "passed" or not r.response_body:
        logger.add_failed("Edge: API key listeleme", "GET", "/v1/api-keys",
                          message="API key listesi alinamadi")
        return

    keys = r.response_body.get("keys", [])
    non_revoked = [k for k in keys if not k.get("is_revoked", False)]
    if len(non_revoked) > 1:
        key_to_revoke = non_revoked[-1]
        key_id = key_to_revoke.get("id")
        r2 = client.revoke_api_key(key_id)
        logger.add(r2)
        if r2.status == "passed":
            logger.add_passed("Edge: API key revoke", "DELETE", f"/v1/api-keys/{key_id}",
                              message="API key basariyla iptal edildi")

            r3 = client.list_api_keys()
            logger.add(r3)
            if r3.status == "passed" and r3.response_body:
                active_keys = r3.response_body.get("keys", [])
                revoked_ids = {k["id"] for k in active_keys if k.get("is_revoked")}
                if key_id in revoked_ids:
                    logger.add_passed("Edge: revoke sonrasi liste", "GET", "/v1/api-keys",
                                      message="Iptal edilen key listede revoked gorunuyor")
        else:
            logger.add_failed("Edge: API key revoke", "DELETE", f"/v1/api-keys/{key_id}",
                              message="API key iptal edilemedi")
