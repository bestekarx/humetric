from pathlib import Path

from ..client import HuMetricClient
from ..logger import ScenarioLogger
from ..runner import ScenarioRunner

PACK_YAML_PATH = Path(__file__).resolve().parents[2] / "packs" / "lastik-bayi.yaml"


def build_lastik_bayi_scenario(runner: ScenarioRunner, client: HuMetricClient,
                               logger: ScenarioLogger) -> None:
    pack_yaml = PACK_YAML_PATH.read_text(encoding="utf-8")

    pack_result = runner.run_pack_ops(pack_yaml, logger)
    if not pack_result:
        return

    entities = [
        {
            "id": "bayi_istanbul_01",
            "entity_type": "bayi",
            "fields": {"bolge": "Istanbul", "satis_adedi": 1200},
            "free_text": "Istanbul Anadolu yakasinda faaliyet gosteren lastik dagitim bayisi. Genis musteri portfoyu ve guclu bayi agi.",
        },
        {
            "id": "bayi_ankara_02",
            "entity_type": "bayi",
            "fields": {"bolge": "Ankara", "satis_adedi": 850},
            "free_text": "Ankara merkezde konumlanmis, agir vasita lastiklerinde uzmanlasmis bayi.",
        },
        {
            "id": "bayi_izmir_03",
            "entity_type": "bayi",
            "fields": {"bolge": "Izmir", "satis_adedi": 650},
            "free_text": "Izmir Alsancak bolgesinde yeni acilmis, buyume potansiyeli yuksek bayi.",
        },
    ]

    created = runner.run_entity_ops(entities, logger)

    signals = [
        {
            "entity_id": "bayi_istanbul_01",
            "entity_type": "bayi",
            "text": "Bayi bu ay %15 buyume gosterdi. Satis hedefinin %110'u yakalandi. Musteri sikayeti yok, tahsilatlar zamaninda yapildi. 3 yeni kurumsal musteri kazanildi.",
        },
        {
            "entity_id": "bayi_istanbul_01",
            "entity_type": "bayi",
            "text": "Istanbul bayisi son ceyrekte pazar payini %5 artirdi. Ozellikle kis lastigi segmentinde guclu performans. Musteri memnuniyeti anketinde 4.7/5 puan aldi.",
        },
        {
            "entity_id": "bayi_ankara_02",
            "entity_type": "bayi",
            "text": "Ankara bayisi agir vasita segmentinde lider konumda. Tahsilat performansi sektor ortalamasinin uzerinde. 2 adet gecikmis odeme var, cozum icin gorusuldu.",
        },
        {
            "entity_id": "bayi_ankara_02",
            "entity_type": "bayi",
            "text": "Ankara bayisi yeni filo anlasmasi imzaladi. Aylik satis hacmi %20 artis gosterdi. Musterilerden olumlu geri donusler aliniyor, ozellikle teknik destek konusunda.",
        },
        {
            "entity_id": "bayi_izmir_03",
            "entity_type": "bayi",
            "text": "Izmir bayisi acilisindan bu yana hizli buyume gosteriyor. Satis hedefleri tutturuluyor, musteri portfoyu genisliyor. Tahsilatlar duzenli.",
        },
        {
            "entity_id": "bayi_izmir_03",
            "entity_type": "bayi",
            "text": "Izmir bayisi Ege bolgesinde taninirligini artiriyor. Yaz lastigi kampanyasinda basarili performans. Musteri sadakati skoru %92.",
        },
    ]

    runner.run_signal_ops(signals, logger)

    queries = [
        {
            "query": "Istanbul bolgesinde satis performansi yuksek bayi",
            "entity_type": "bayi",
            "top_k": 2,
        },
        {
            "query": "tahsilat disiplini en iyi bayi",
            "entity_type": "bayi",
            "top_k": 3,
        },
    ]
    runner.run_query_test(queries, logger)
