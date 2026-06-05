import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOGS_DIR = ROOT / "logs"


class StepResult:
    def __init__(
        self,
        step: str,
        status: str,
        method: str,
        url: str,
        request_body: dict | None = None,
        response_status: int = 0,
        response_body: dict | None = None,
        elapsed_ms: int = 0,
        message: str = "",
    ):
        self.step = step
        self.status = status
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.method = method
        self.url = url
        self.request_body = request_body
        self.response_status = response_status
        self.elapsed_ms = elapsed_ms
        self.response_body = response_body
        self.message = message

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "status": self.status,
            "timestamp": self.timestamp,
            "method": self.method,
            "url": self.url,
            "elapsed_ms": self.elapsed_ms,
            "request_body": self.request_body,
            "response_status": self.response_status,
            "response_body": self.response_body,
            "message": self.message,
        }


class ScenarioLogger:
    def __init__(self, scenario_name: str):
        self.scenario_name = scenario_name
        self.steps: list[StepResult] = []
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.finished_at = ""

    def add(self, result: StepResult) -> None:
        self.steps.append(result)

    def add_passed(self, step: str, method: str, url: str, request_body: dict | None = None,
                   response_status: int = 0, response_body: dict | None = None, elapsed_ms: int = 0,
                   message: str = "") -> StepResult:
        r = StepResult(step, "passed", method, url, request_body, response_status, response_body, elapsed_ms, message)
        self.add(r)
        return r

    def add_failed(self, step: str, method: str, url: str, request_body: dict | None = None,
                   response_status: int = 0, response_body: dict | None = None, elapsed_ms: int = 0,
                   message: str = "") -> StepResult:
        r = StepResult(step, "failed", method, url, request_body, response_status, response_body, elapsed_ms, message)
        self.add(r)
        return r

    def add_skipped(self, step: str, method: str = "", url: str = "", message: str = "") -> StepResult:
        r = StepResult(step, "skipped", method, url, None, 0, None, 0, message)
        self.add(r)
        return r

    @property
    def summary(self) -> dict:
        passed = sum(1 for s in self.steps if s.status == "passed")
        failed = sum(1 for s in self.steps if s.status == "failed")
        skipped = sum(1 for s in self.steps if s.status == "skipped")
        total_elapsed = sum(s.elapsed_ms for s in self.steps)
        return {
            "scenario": self.scenario_name,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "elapsed_ms": total_elapsed,
        }

    def flush(self) -> str:
        self.finished_at = datetime.now(timezone.utc).isoformat()
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"{self.scenario_name}_{timestamp}.json"
        filepath = LOGS_DIR / filename
        report = {
            "scenario": self.scenario_name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_elapsed_ms": sum(s.elapsed_ms for s in self.steps),
            "steps": [s.to_dict() for s in self.steps],
            "summary": self.summary,
        }
        filepath.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(filepath)

    @staticmethod
    def generate_summary(scenario_reports: list[dict]) -> str:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        total_passed = sum(r["passed"] for r in scenario_reports)
        total_failed = sum(r["failed"] for r in scenario_reports)
        total_skipped = sum(r["skipped"] for r in scenario_reports)
        total_elapsed = sum(r["elapsed_ms"] for r in scenario_reports)
        summary = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scenarios": [
                {
                    "name": r["scenario"],
                    "status": "passed" if r["failed"] == 0 else "failed",
                    "passed": r["passed"],
                    "failed": r["failed"],
                    "skipped": r["skipped"],
                    "elapsed_ms": r["elapsed_ms"],
                }
                for r in scenario_reports
            ],
            "total": {
                "passed": total_passed,
                "failed": total_failed,
                "skipped": total_skipped,
                "elapsed_ms": total_elapsed,
            },
        }
        filepath = LOGS_DIR / "summary.json"
        filepath.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(filepath)
