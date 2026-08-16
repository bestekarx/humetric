from pathlib import Path

from ..client import HuMetricClient
from ..logger import ScenarioLogger
from ..runner import ScenarioRunner

PACK_YAML_PATH = Path(__file__).resolve().parents[2] / "packs" / "otel-tesis.yaml"


def build_otel_tesis_scenario(runner: ScenarioRunner, client: HuMetricClient,
                              logger: ScenarioLogger) -> None:
    pack_yaml = PACK_YAML_PATH.read_text(encoding="utf-8")

    pack_result = runner.run_pack_ops(pack_yaml, logger)
    if not pack_result:
        return

    entities = [
        {
            "id": "tesis_istanbul_01",
            "entity_type": "tesis",
            "fields": {"bolge": "Istanbul", "oda_sayisi": 180},
            "free_text": "Istanbul Sultanahmet'te tarihi binada hizmet veren sehir oteli. Agirlikli olarak yabanci misafir.",
        },
        {
            "id": "tesis_antalya_02",
            "entity_type": "tesis",
            "fields": {"bolge": "Antalya", "oda_sayisi": 320},
            "free_text": "Antalya sahilinde her sey dahil calisan resort. Yaz sezonunda doluluk yuksek.",
        },
        {
            "id": "tesis_izmir_03",
            "entity_type": "tesis",
            "fields": {"bolge": "Izmir", "oda_sayisi": 42},
            "free_text": "Izmir Alacati'da yeni acilmis butik otel. Sezon disi doluluk dusuk.",
        },
    ]

    created = runner.run_entity_ops(entities, logger)

    signals = [
        {
            "entity_id": "tesis_istanbul_01",
            "entity_type": "tesis",
            "text": "[SISTEM VERISI] Acik ariza kaydi: 0. Denetim skoru 96/100. Iptal orani %3.\n[MISAFIR YORUMU] Odaya girdigimizde her yer pirilpirildi, banyoda tek bir leke yoktu. Resepsiyon gece yarisi aradigimizda 10 dakikada cozum uretti.",
        },
        {
            "entity_id": "tesis_istanbul_01",
            "entity_type": "tesis",
            "text": "[SISTEM VERISI] Son ceyrekte gurultu sikayeti 12'den 2'ye dustu. Ses yalitimi yatirimi tamamlandi.\n[MISAFIR YORUMU] Cadde uzerinde olmasina ragmen odada hic ses duyulmuyor. Bu 4. konaklamamiz, arkadaslarimiza da tavsiye ettik.",
        },
        {
            "entity_id": "tesis_antalya_02",
            "entity_type": "tesis",
            "text": "[SISTEM VERISI] 14 odada acik ariza kaydi var, bazilari 3 haftadir kapatilmamis. Temizlik skoru 61/100.\n[MISAFIR YORUMU] Odaya girdigimizde carsaflar degismemisti, banyoda kuf kokusu vardi. Oda degistirmek zorunda kaldik.",
        },
        {
            "entity_id": "tesis_antalya_02",
            "entity_type": "tesis",
            "text": "[SISTEM VERISI] Yuksek sezon dolulugu %94. Erken cikis talebi bu ay 7.\n[MISAFIR YORUMU] Resepsiyonda 25 dakika bekledik, kimse ilgilenmedi. Bir daha gelmeyiz, bu fiyata cok daha iyi alternatif var.",
        },
        {
            "entity_id": "tesis_izmir_03",
            "entity_type": "tesis",
            "text": "[SISTEM VERISI] Denetim skoru 92/100. Acik ariza kaydi 1, ayni gun kapatildi.\n[MISAFIR YORUMU] Kahvaltida cocugumuz icin ayri menu hazirladilar, kimse istemeden dusunmusler. Yatak cok rahatti.",
        },
        {
            "entity_id": "tesis_izmir_03",
            "entity_type": "tesis",
            "text": "[SISTEM VERISI] Sezon disi doluluk %38, tekrar konaklama orani %41.\n[MISAFIR YORUMU] Konum ve temizlik iyiydi ama havuz beklendigimiz gibi degildi. Fiyatina gore normal.",
        },
    ]

    runner.run_signal_ops(signals, logger)

    queries = [
        {
            "query": "temizlik ve bakim skoru yuksek tesis",
            "entity_type": "tesis",
            "top_k": 2,
        },
        {
            "query": "misafirin tekrar gelme egilimi en dusuk tesis",
            "entity_type": "tesis",
            "top_k": 3,
        },
    ]
    runner.run_query_test(queries, logger)
