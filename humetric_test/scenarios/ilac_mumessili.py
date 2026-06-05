from ..client import HuMetricClient
from ..logger import ScenarioLogger
from ..runner import ScenarioRunner


def build_ilac_mumessili_scenario(runner: ScenarioRunner, client: HuMetricClient,
                                   logger: ScenarioLogger) -> None:
    domain_description = (
        "Ilac pazarlama mumessili: Hekim ziyareti yapar, numune dagitimi gerceklestirir, "
        "bolge yonetimi yapar. Performans metrikleri olarak ziyaret sikligi, hekim iliskisi, "
        "numune dagitim performansi, raporlama disiplini ve satis destegi olculur. "
        "Zorunlu alanlar: bolge (str), ziyaret_hedefi (int)."
    )

    wizard_result = client.create_pack_wizard(domain_description)
    logger.add(wizard_result)
    if wizard_result.status != "passed" or not wizard_result.response_body:
        logger.add_failed("Wizard ile pack olusturma", "POST", "/v1/packs/wizard",
                          message="Wizard pack olusturamadi")
        return

    wizard_data = wizard_result.response_body
    # API yaniti: PackWizardResponse.pack_yaml (eski testte 'yaml' bekleniyordu).
    pack_yaml = wizard_data.get("pack_yaml") or wizard_data.get("yaml")

    if not pack_yaml:
        logger.add_failed("Wizard pack YAML", "POST", "/v1/packs/wizard",
                          message="Wizard YAML dondurmedi")
        return

    import yaml
    pack_def = yaml.safe_load(pack_yaml)
    pack_key = wizard_data.get("pack_key") or pack_def.get("entity_type", "ilac-mumessili")
    pack_def["pack_key"] = pack_key

    pack_result = runner.run_pack_ops(pack_yaml, logger)
    if not pack_result:
        return

    entities = [
        {
            "id": "mumessil_deniz",
            "entity_type": pack_def.get("entity_type", "mumessil"),
            "fields": {"bolge": "Istanbul Anadolu", "ziyaret_hedefi": 80},
            "free_text": "Deniz Bey, Istanbul Anadolu yakasinda 5 yillik deneyimli ilac mumessili. Hekim iliskileri kuvvetli.",
        },
        {
            "id": "mumessil_zeynep",
            "entity_type": pack_def.get("entity_type", "mumessil"),
            "fields": {"bolge": "Ankara", "ziyaret_hedefi": 65},
            "free_text": "Zeynep Hanim, Ankara bolgesinde universite hastanelerinde uzmanlasmis deneyimli mumessil.",
        },
        {
            "id": "mumessil_burak",
            "entity_type": pack_def.get("entity_type", "mumessil"),
            "fields": {"bolge": "Izmir", "ziyaret_hedefi": 50},
            "free_text": "Burak Bey, Izmir Ege bolgesinde yeni baslamis, gelisime acik ilac mumessili.",
        },
    ]

    runner.run_entity_ops(entities, logger)

    signals = [
        {
            "entity_id": "mumessil_deniz",
            "entity_type": pack_def.get("entity_type", "mumessil"),
            "text": "Deniz Bey bu ay 85 hekim ziyareti gerceklestirerek hedefin uzerine cikti. Numune dagitimi duzenli, hekim geri bildirimleri olumlu. Raporlamalari zamaninda yapiyor. Ozellikle kardiyoloji alaninda guclu iliskiler kurdu.",
        },
        {
            "entity_id": "mumessil_deniz",
            "entity_type": pack_def.get("entity_type", "mumessil"),
            "text": "Deniz Bey son ceyrekte satis desteginde one cikti. 3 yeni urun lansmaninda aktif rol aldi. Hekimlerden gelen urun taleplerini hizla iletiyor. Ekip arkadaslarina mentorluk yapiyor.",
        },
        {
            "entity_id": "mumessil_zeynep",
            "entity_type": pack_def.get("entity_type", "mumessil"),
            "text": "Zeynep Hanim universite hastanelerinde 70 ziyaret gerceklestirdi. Numune dagitimi ve takibi cok duzenli. Hekimlerle uzun soluklu iliskiler gelistiriyor. Raporlama kalitesi yuksek.",
        },
        {
            "entity_id": "mumessil_zeynep",
            "entity_type": pack_def.get("entity_type", "mumessil"),
            "text": "Zeynep Hanim bu donem 2 buyuk kongrede gorev aldi. Akademik hekimlerle iletisimi cok iyi. Yeni urun sunumlarinda basarili performans. Satis ekibine degerli saha bilgisi sagliyor.",
        },
        {
            "entity_id": "mumessil_burak",
            "entity_type": pack_def.get("entity_type", "mumessil"),
            "text": "Burak Bey ilk aylarinda 45 hekim ziyareti gerceklestirdi, hedefine yakin. Ogrenmeye acik ve istekli. Raporlamalari bazen gecikmeli ama gelisim gosteriyor.",
        },
        {
            "entity_id": "mumessil_burak",
            "entity_type": pack_def.get("entity_type", "mumessil"),
            "text": "Burak Bey bu ay hedefini tam tutturdu (50 ziyaret). Numune dagitimini duzene soktu. Bolge hekimleriyle iliskileri gelisiyor. Satis destegi konusunda daha aktif olmasi bekleniyor.",
        },
    ]

    runner.run_signal_ops(signals, logger)

    queries = [
        {
            "query": "ziyaret performansi en yuksek mumessil",
            "entity_type": pack_def.get("entity_type", "mumessil"),
            "top_k": 3,
        },
    ]
    runner.run_query_test(queries, logger)
