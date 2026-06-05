import argparse
import sys
import uuid

from .client import HuMetricClient
from .logger import ScenarioLogger
from .runner import ScenarioRunner
from .scenarios.lastik_bayi import build_lastik_bayi_scenario
from .scenarios.saha_isci import build_saha_isci_scenario
from .scenarios.ilac_mumessili import build_ilac_mumessili_scenario
from .scenarios.edge_cases import build_edge_cases_scenario

SCENARIOS = {
    "lastik_bayi": ("Lastik Dagitim Bayisi", build_lastik_bayi_scenario),
    "saha_isci": ("Saha Hizmet Iscisi", build_saha_isci_scenario),
    "ilac_mumessili": ("Ilac Pazarlama Mumessili", build_ilac_mumessili_scenario),
    "edge_cases": ("Edge Case Testleri", build_edge_cases_scenario),
}


def main():
    parser = argparse.ArgumentParser(description="HuMetric API Test Harness")
    parser.add_argument("--scenario", type=str, default=None,
                        help="Belirli bir senaryoyu calistir (register, lastik_bayi, saha_isci, ilac_mumessili, edge_cases)")
    parser.add_argument("--verbose", action="store_true", help="Detayli cikti")
    args = parser.parse_args()

    valid_scenarios = list(SCENARIOS.keys()) + ["register"]
    if args.scenario and args.scenario not in valid_scenarios:
        print(f"HATA: Bilinmeyen senaryo '{args.scenario}'. Kullanilabilir: {', '.join(valid_scenarios)}")
        sys.exit(1)

    client = HuMetricClient()
    runner = ScenarioRunner(client, verbose=args.verbose)

    print("=== HuMetric Test Harness ===")
    print("API: http://localhost:8002")
    print()

    test_email = f"test-{uuid.uuid4().hex[:8]}@humetric.local"
    test_password = "Test1234!Test"

    reg_logger = ScenarioLogger("register")

    print("[1/4] Tenant olusturuluyor...")
    reg_result = runner.run_register(test_email, test_password, reg_logger)
    if not reg_result:
        print("  X Register basarisiz. Sonlandiriliyor.")
        reg_report = reg_logger.flush()
        print(f"  Log: {reg_report}")
        sys.exit(1)

    print(f"  Tenant: {reg_result['tenant_id']}")

    key_logger = ScenarioLogger("api_keys")

    test_key = runner.run_create_full_scope_key(key_logger)
    if not test_key:
        print("  X Full-scope API key olusturulamadi. Sonlandiriliyor.")
        key_report = key_logger.flush()
        print(f"  Log: {key_report}")
        sys.exit(1)

    print("  Full-scope API key olusturuldu")
    key_logger.flush()

    all_reports = []

    if args.scenario == "register":
        reg_logger.flush()
        key_path = ScenarioLogger.generate_summary(all_reports)
        print("\n" + "-" * 40)
        print("Register akisi tamamlandi.")
        print(f"Loglar: {key_path}")
        client.close()
        return

    scenarios_to_run = SCENARIOS
    if args.scenario:
        scenarios_to_run = {args.scenario: SCENARIOS[args.scenario]}

    idx = 2
    total_scenarios = len(scenarios_to_run) + 2
    for name, (label, builder) in scenarios_to_run.items():
        print(f"\n[{idx}/{total_scenarios}] Senaryo: {name}")
        logger = ScenarioLogger(name)

        try:
            scenario = builder(runner, client, logger)
            all_reports.append(logger.summary)
            print(f"  Ozet: {logger.summary['passed']} passed, {logger.summary['failed']} failed, "
                  f"{logger.summary['skipped']} skipped")
        except Exception as exc:
            print(f"  X Senaryo hatasi: {exc}")
            logger.add_failed(f"scenario:{name}", "", "", message=str(exc))
            all_reports.append(logger.summary)

        logger.flush()
        idx += 1

    summary_path = ScenarioLogger.generate_summary(all_reports)
    reg_logger.flush()

    print("\n" + "-" * 40)
    total_p = sum(r["passed"] for r in all_reports)
    total_f = sum(r["failed"] for r in all_reports)
    total_s = sum(r["skipped"] for r in all_reports)
    print(f"Toplam: {total_p} passed, {total_f} failed, {total_s} skipped")
    print(f"Loglar: {summary_path}")

    client.close()


if __name__ == "__main__":
    main()
