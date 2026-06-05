from pathlib import Path

from ..client import HuMetricClient
from ..logger import ScenarioLogger
from ..runner import ScenarioRunner

PACK_YAML_PATH = Path(__file__).resolve().parents[2] / "packs" / "saha-hizmet-isci.yaml"


def build_saha_isci_scenario(runner: ScenarioRunner, client: HuMetricClient,
                             logger: ScenarioLogger) -> None:
    pack_yaml = PACK_YAML_PATH.read_text(encoding="utf-8")

    pack_result = runner.run_pack_ops(pack_yaml, logger)
    if not pack_result:
        return

    entities = [
        {
            "id": "isci_ahmet",
            "entity_type": "isci",
            "fields": {"lokasyon": "Istanbul Avrupa", "statik_beceriler": "Tesisat, Su tesisati, Kalorifer"},
            "free_text": "Ahmet Bey, 12 yillik deneyimli tesisat ustasi. Profesyonel ve duzenli calisir.",
        },
        {
            "id": "isci_mehmet",
            "entity_type": "isci",
            "fields": {"lokasyon": "Ankara Cankaya", "statik_beceriler": "Elektrik, Elektrik tesisati, Ariza tespit"},
            "free_text": "Mehmet Bey, elektrik alaninda 8 yillik tecrubeli. Acil durumlarda hizli mudahale.",
        },
        {
            "id": "isci_ayse",
            "entity_type": "isci",
            "fields": {"lokasyon": "Izmir Karsiyaka", "statik_beceriler": "Boyama, Dekorasyon, Tadilat"},
            "free_text": "Ayse Hanim, boya ve dekorasyon alaninda 6 yillik deneyime sahip. Titiz ve detayli calisir.",
        },
    ]

    created = runner.run_entity_ops(entities, logger)

    signals = [
        {
            "entity_id": "isci_ahmet",
            "entity_type": "isci",
            "text": "Ahmet usta bugunku ise tam zamaninda geldi. Tesisat arizasini 45 dakikada cozdu, isciligi cok temiz. Musteri cok memnun kaldi.",
        },
        {
            "entity_id": "isci_ahmet",
            "entity_type": "isci",
            "text": "Ahmet usta haftalik degerlendirmede yuksek puan aldi. Tum randevularina dakik gitti, is bitirme suresi ortalamanin altinda.",
        },
        {
            "entity_id": "isci_mehmet",
            "entity_type": "isci",
            "text": "Mehmet usta acil elektrik arizasina 20 dakikada ulasti. Ariza tespiti ve onarimi hizli yapildi. Musteri hizli mudahaleden dolayi tesekkur etti.",
        },
        {
            "entity_id": "isci_mehmet",
            "entity_type": "isci",
            "text": "Mehmet usta isletmeye gec kaldi, malzeme eksikligi nedeniyle is yarim kaldi. Ertesi gun tamamlandi ama musteri rahatsiz oldu.",
        },
        {
            "entity_id": "isci_ayse",
            "entity_type": "isci",
            "text": "Ayse hanim boya isini cok temiz yapti, calisma alanini duzenli tuttu. Musteriyle iletisimi harika, surec hakkinda duzgun bilgilendirme yapti.",
        },
        {
            "entity_id": "isci_ayse",
            "entity_type": "isci",
            "text": "Ayse hanim bu hafta 3 isi basariyla tamamladi. Tum musteri yorumlari olumlu, ozellikle is titizligi ve nezaket konusunda ovgu aldi.",
        },
    ]

    runner.run_signal_ops(signals, logger)

    queries = [
        {
            "query": "dakiklik ve musteri iletisimi yuksek isci",
            "entity_type": "isci",
            "top_k": 2,
        },
    ]
    runner.run_query_test(queries, logger)

    kvkk_entity = {
        "id": "isci_kvkk_test",
        "entity_type": "isci",
        "fields": {"lokasyon": "Bursa", "statik_beceriler": "Tesisat, Kombi"},
        "free_text": "KVKK test entity: mali durum hassas metrik testi",
    }
    runner.run_entity_ops([kvkk_entity], logger)

    logger.add_passed("KVKK: consent yokken sinyal gonderiliyor", "", "")
    kvkk_signal_1 = {
        "entity_id": "isci_kvkk_test",
        "entity_type": "isci",
        "text": "Isci bu ay maasini zamaninda aldi, finansal durumu stabil gorunuyor. Ek gelir icin hafta sonu calismalari yapiyor.",
    }
    runner.run_signal_ops([kvkk_signal_1], logger)

    r_entity = client.get_entity("isci_kvkk_test")
    logger.add(r_entity)
    if r_entity.status == "passed" and r_entity.response_body:
        metrics = r_entity.response_body.get("metrics", [])
        metric_keys = {m.get("metric_key") for m in metrics}
        if "mali_durum" in metric_keys:
            logger.add_failed("KVKK: consent yok → mali_durum gizli", "GET", "/v1/entities/isci_kvkk_test",
                              message="HASSAS METRIK CONSENT YOKKEN GORUNUYOR!")
        else:
            logger.add_passed("KVKK: consent yok → mali_durum gizli", "GET", "/v1/entities/isci_kvkk_test",
                              message="Hassas metrik basariyla gizlendi")

    logger.add_passed("KVKK: consent veriliyor", "", "")
    runner.run_consent_ops("isci_kvkk_test", "hassas_veri_isleme", logger, grant=True)

    kvkk_signal_2 = {
        "entity_id": "isci_kvkk_test",
        "entity_type": "isci",
        "text": "Isci finansal acidan oldukca duzenli, tum faturalari zamaninda odeniyor. Kredi notu yuksek, mali disiplinli.",
    }
    runner.run_signal_ops([kvkk_signal_2], logger)

    r_entity2 = client.get_entity("isci_kvkk_test")
    logger.add(r_entity2)
    if r_entity2.status == "passed" and r_entity2.response_body:
        metrics = r_entity2.response_body.get("metrics", [])
        metric_keys = {m.get("metric_key") for m in metrics}
        if "mali_durum" in metric_keys:
            logger.add_passed("KVKK: consent var → mali_durum gorunur", "GET", "/v1/entities/isci_kvkk_test",
                              message="Hassas metrik consent ile gorunur oldu")
        else:
            logger.add_failed("KVKK: consent var → mali_durum gorunur", "GET", "/v1/entities/isci_kvkk_test",
                              message="HASSAS METRIK CONSENT VAREN GORUNMUYOR!")

    runner.run_consent_ops("isci_kvkk_test", "hassas_veri_isleme", logger, grant=False)

    r_entity3 = client.get_entity("isci_kvkk_test")
    logger.add(r_entity3)
    if r_entity3.status == "passed" and r_entity3.response_body:
        metrics = r_entity3.response_body.get("metrics", [])
        metric_keys = {m.get("metric_key") for m in metrics}
        if "mali_durum" in metric_keys:
            logger.add_failed("KVKK: consent kaldirildi → mali_durum gizli", "GET", "/v1/entities/isci_kvkk_test",
                              message="HASSAS METRIK CONSENT KALKINCA HALA GORUNUYOR!")
        else:
            logger.add_passed("KVKK: consent kaldirildi → mali_durum gizli", "GET", "/v1/entities/isci_kvkk_test",
                              message="Hassas metrik consent kalkinca gizlendi")
