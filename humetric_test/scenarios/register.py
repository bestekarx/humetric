from ..client import HuMetricClient
from ..logger import ScenarioLogger
from ..runner import ScenarioRunner


def build_register_scenario(runner: ScenarioRunner, client: HuMetricClient,
                            logger: ScenarioLogger) -> None:
    logger.add_passed("Register akisi testi", "", "",
                      message="Register akisi __main__ icinde calistirildi. Detaylar icin register log dosyasina bakin.")
