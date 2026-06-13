from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import config
from .agents import curator, extractor
from .agents.versioning import hash_prompt, hash_schema, hash_text
from .schema import ExtractedMetric, ExtractionResult, FinalMetric

_log = logging.getLogger(__name__)

CANARY_DIR = Path(__file__).resolve().parents[2] / "packs" / "canary"


def _load_canary(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _serialize_signal(signal: dict) -> str:
    text = signal.get("signal_text", "") or ""
    structured = signal.get("structured")
    if not text and structured:
        text = json.dumps(structured, sort_keys=True, ensure_ascii=False)
    return text


async def _run_extraction(
    signal_text: str,
    entity_context: str,
    pack_prompt: str | None = None,
    pack_metrics: list[dict] | None = None,
) -> tuple[list[ExtractedMetric], dict]:
    call_meta: dict = {}
    extracted = await extractor.extract_metrics(
        signal_text,
        entity_context,
        pack_prompt=pack_prompt,
        pack_metrics=pack_metrics,
        call_meta=call_meta,
    )
    return extracted, call_meta


async def _run_curation(
    extracted: list[ExtractedMetric],
    entity_context: str,
    pack_def: dict | None = None,
) -> tuple[list[FinalMetric], dict]:
    call_meta: dict = {}
    final_metrics = await curator.curate_metrics(
        extracted,
        [],
        entity_context,
        pack_def=pack_def,
        call_meta=call_meta,
    )
    return final_metrics, call_meta


def _value_in_range(value: float, vr: list[float]) -> bool:
    if len(vr) != 2:
        return False
    return vr[0] <= value <= vr[1]


def _check_extraction(expected: dict, extracted_list: list[ExtractedMetric]) -> list[dict]:
    results: list[dict] = []
    extracted_map = {e.metric_key: e for e in extracted_list}

    for exp in expected.get("expected_extraction", []):
        key = exp["metric_key"]
        actual = extracted_map.get(key)
        entry: dict = {
            "metric_key": key,
            "status": "missing",
        }
        if actual:
            entry["actual_value"] = actual.value
            entry["actual_confidence"] = actual.confidence
            entry["needs_review"] = actual.needs_review

            vr = exp.get("value_range", [-2, 2])
            if _value_in_range(actual.value, vr):
                conf_min = exp.get("confidence_min", 0.0)
                if actual.confidence >= conf_min:
                    entry["status"] = "passed"
                else:
                    entry["status"] = "low_confidence"
                    entry["confidence_diff"] = actual.confidence - conf_min
            else:
                entry["status"] = "value_out_of_range"
                entry["expected_range"] = vr

        results.append(entry)

    allowed = set(expected.get("allowed_unknown", []))
    for e in extracted_list:
        if e.metric_key not in {r["metric_key"] for r in results}:
            if e.metric_key in allowed:
                continue
            if e.needs_review or e.confidence <= 0.0:
                results.append({
                    "metric_key": e.metric_key,
                    "status": "needs_review",
                    "actual_value": e.value,
                    "actual_confidence": e.confidence,
                })
            else:
                results.append({
                    "metric_key": e.metric_key,
                    "status": "unexpected_metric",
                    "actual_value": e.value,
                    "actual_confidence": e.confidence,
                })

    return results


def _check_curation(expected: dict, final_list: list[FinalMetric], extracted_list: list[ExtractedMetric]) -> list[dict]:
    results: list[dict] = []
    final_map = {f.metric_key: f for f in final_list}
    extracted_map = {e.metric_key: e for e in extracted_list}
    expected_actions = expected.get("expected_curation", {}).get("actions", {})

    for key, allowed_actions in expected_actions.items():
        actual = final_map.get(key)
        ext = extracted_map.get(key)
        entry: dict = {
            "metric_key": key,
            "status": "missing",
        }
        if actual:
            entry["actual_value"] = actual.value
            entry["actual_confidence"] = actual.confidence
            entry["needs_review"] = actual.needs_review
            entry["status"] = "present"
        elif ext and (ext.needs_review or ext.confidence <= 0.0):
            entry["status"] = "filtered_needs_review"
        elif ext and ext.confidence < config.CONFIDENCE_THRESHOLD:
            entry["status"] = "filtered_low_confidence"
            entry["extracted_confidence"] = ext.confidence
        elif ext:
            entry["status"] = "filtered_by_curator"
            entry["extracted_value"] = ext.value
            entry["extracted_confidence"] = ext.confidence

        results.append(entry)

    for f in final_list:
        if f.metric_key not in {r["metric_key"] for r in results}:
            results.append({
                "metric_key": f.metric_key,
                "status": "unexpected_metric",
                "actual_value": f.value,
                "actual_confidence": f.confidence,
            })

    return results


async def _replay_canary(canary: dict, pack_def: dict | None = None) -> dict:
    signals = canary.get("signals", [])
    pack_prompt = None
    pack_metrics = None
    if pack_def:
        prompts = pack_def.get("prompts", {}) or {}
        pack_prompt = prompts.get("extraction")
        pack_metrics = pack_def.get("metrics", [])

    per_signal: list[dict] = []
    summary = {
        "total_signals": len(signals),
        "passed": 0,
        "failed": 0,
        "warnings": 0,
        "extraction_drift": 0,
        "curation_drift": 0,
    }

    for sig in signals:
        sig_id = sig["id"]
        signal_text = _serialize_signal(sig)
        entity_context = sig.get("entity_context", "")

        input_hash = hash_text(signal_text)

        extracted, extract_meta = await _run_extraction(
            signal_text, entity_context, pack_prompt, pack_metrics,
        )
        final_metrics, curator_meta = await _run_curation(
            extracted, entity_context, pack_def,
        )

        ext_results = _check_extraction(sig, extracted)
        cur_results = _check_curation(sig, final_metrics, extracted)

        sig_passed = all(
            r["status"] in ("passed", "present")
            for r in ext_results + cur_results
            if r["status"] not in ("missing",)
        )
        sig_failed = any(
            r["status"] in ("value_out_of_range", "low_confidence")
            for r in ext_results
        )

        if sig_passed and not sig_failed:
            summary["passed"] += 1
        elif sig_failed:
            summary["failed"] += 1
        else:
            summary["warnings"] += 1

        per_signal.append({
            "signal_id": sig_id,
            "signal_text": signal_text[:200],
            "input_hash": input_hash,
            "extract_prompt_hash": extract_meta.get("prompt_hash"),
            "extract_schema_hash": extract_meta.get("schema_hash"),
            "extract_model": extract_meta.get("model"),
            "curator_prompt_hash": curator_meta.get("prompt_hash"),
            "curator_schema_hash": curator_meta.get("schema_hash"),
            "curator_model": curator_meta.get("model"),
            "extraction": ext_results,
            "curation": cur_results,
        })

    summary["extraction_drift"] = sum(
        1 for s in per_signal
        for r in s["extraction"]
        if r["status"] in ("value_out_of_range", "low_confidence")
    )
    summary["curation_drift"] = sum(
        1 for s in per_signal
        for r in s["curation"]
        if r["status"] == "unexpected_metric"
    )

    run_ts = datetime.now(timezone.utc).isoformat()
    prompt_hash_val = hash_prompt(pack_prompt or _load_default_prompt())
    schema_hash_val = hash_schema(ExtractionResult)

    return {
        "run_id": f"replay-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
        "run_ts": run_ts,
        "pack": canary.get("pack_key"),
        "prompt_hash": prompt_hash_val,
        "schema_hash": schema_hash_val,
        "model": config.AGENT_MODEL,
        "curator_model": config.CURATOR_MODEL,
        "summary": summary,
        "per_signal": per_signal,
    }


def _load_default_prompt() -> str:
    from .agents import _load_prompt
    return _load_prompt("extractor-default") or ""


def _load_pack_definition(pack_key: str) -> dict | None:
    pack_path = Path(__file__).resolve().parents[2] / "packs" / f"{pack_key}.yaml"
    if not pack_path.exists():
        return None
    with open(pack_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _compare_runs(prev_report: dict, current_report: dict) -> dict:
    prev_signals = {s["signal_id"]: s for s in prev_report.get("per_signal", [])}
    curr_signals = {s["signal_id"]: s for s in current_report.get("per_signal", [])}

    diffs: list[dict] = []

    for sig_id, prev in prev_signals.items():
        curr = curr_signals.get(sig_id)
        if not curr:
            continue

        for p_ext in prev.get("extraction", []):
            key = p_ext["metric_key"]
            c_entries = [e for e in curr.get("extraction", []) if e["metric_key"] == key]
            c_ext = c_entries[0] if c_entries else None

            if p_ext.get("status") == "passed" and c_ext and c_ext.get("status") != "passed":
                diffs.append({
                    "signal_id": sig_id,
                    "stage": "extraction",
                    "metric_key": key,
                    "previous_status": p_ext["status"],
                    "current_status": c_ext["status"],
                    "previous_value": p_ext.get("actual_value"),
                    "current_value": c_ext.get("actual_value"),
                })

    for sig_id, prev in prev_signals.items():
        curr = curr_signals.get(sig_id)
        if not curr:
            continue

        prev_keys = {r["metric_key"] for r in prev.get("extraction", [])}
        curr_keys = {r["metric_key"] for r in curr.get("extraction", [])}

        missing = prev_keys - curr_keys
        new = curr_keys - prev_keys

        for mk in missing:
            diffs.append({
                "signal_id": sig_id,
                "stage": "extraction",
                "metric_key": mk,
                "type": "missing",
            })
        for mk in new:
            diffs.append({
                "signal_id": sig_id,
                "stage": "extraction",
                "metric_key": mk,
                "type": "new",
            })

    return {
        "previous_run_id": prev_report.get("run_id"),
        "current_run_id": current_report.get("run_id"),
        "total_diffs": len(diffs),
        "diffs": diffs,
    }


def _format_report(report: dict, verbose: bool = False) -> str:
    s = report["summary"]
    lines: list[str] = []
    lines.append(f"Run ID:    {report['run_id']}")
    lines.append(f"Pack:      {report['pack']}")
    lines.append(f"Model:     {report['model']} (extract) / {report['curator_model']} (curate)")
    lines.append(f"Prompt:    {report['prompt_hash'][:12]}...")
    lines.append(f"Schema:    {report['schema_hash'][:12]}...")
    lines.append("")
    lines.append(f"Signals:   {s['total_signals']} total | {s['passed']} passed | {s['failed']} failed | {s['warnings']} warnings")
    lines.append(f"Drift:     {s['extraction_drift']} extraction | {s['curation_drift']} curation")
    lines.append("")

    if verbose:
        for sig in report.get("per_signal", []):
            statuses: list[str] = []
            for r in sig["extraction"]:
                statuses.append(f"  {r['metric_key']}: {r['status']}")
                if "actual_value" in r:
                    statuses[-1] += f" (val={r['actual_value']})"
            if statuses:
                lines.append(f"--- {sig['signal_id']} ---")
                lines.extend(statuses)

    return "\n".join(lines)


def _format_compare(compare: dict) -> str:
    lines: list[str] = []
    lines.append(f"Previous:  {compare['previous_run_id']}")
    lines.append(f"Current:   {compare['current_run_id']}")
    lines.append(f"Total diffs: {compare['total_diffs']}")
    for d in compare["diffs"]:
        t = d.get("type", d.get("current_status", "changed"))
        lines.append(f"  {d['signal_id']} / {d['stage']} / {d['metric_key']}: {t}")
    return "\n".join(lines)


async def _main(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    canary_path = Path(args.canary_file) if args.canary_file else CANARY_DIR / f"{args.pack}-canary.yaml"
    if not canary_path.exists():
        _log.error("Canary file not found: %s", canary_path)
        return 1

    output_path = Path(args.output) if args.output else None

    if args.compare_run:
        if not output_path or not output_path.exists():
            _log.error("--compare-run requires --output pointing to a previous report")
            return 1
        prev = json.loads(output_path.read_text(encoding="utf-8"))
        canary = _load_canary(canary_path)
        pack_def = _load_pack_definition(args.pack) if args.pack else canary.get("pack_definition", {}) if isinstance(canary.get("pack_definition"), dict) else {}
        current = await _replay_canary(canary, pack_def)
        comparison = _compare_runs(prev, current)
        if args.output:
            report_path = Path(args.output)
            if not report_path.parent.exists():
                report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        print(_format_compare(comparison))
        return 0 if comparison["total_diffs"] == 0 else 1

    canary = _load_canary(canary_path)
    pack_def = _load_pack_definition(args.pack) if args.pack else {}
    report = await _replay_canary(canary, pack_def)

    if output_path:
        if not output_path.parent.exists():
            output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        _log.info("Report written to %s", output_path)

    print(_format_report(report, verbose=args.verbose))

    if args.ci:
        max_drift = args.max_drift
        extraction_drift = report["summary"]["extraction_drift"]
        failed = report["summary"]["failed"]
        if extraction_drift > max_drift or failed > 0:
            _log.error("CI check failed: extraction_drift=%d > max=%d, failed=%d", extraction_drift, max_drift, failed)
            return 1
        _log.info("CI check passed: extraction_drift=%d <= max=%d, failed=%d", extraction_drift, max_drift, failed)

    return 0


def main():
    parser = argparse.ArgumentParser(description="HuMetric canary replay harness")
    parser.add_argument("--pack", required=True, help="Pack key (e.g. saha-hizmet-isci)")
    parser.add_argument("--canary-file", help="Path to canary YAML file (default: packs/canary/<pack>-canary.yaml)")
    parser.add_argument("--output", "-o", help="Write JSON report to file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose per-signal output")
    parser.add_argument("--ci", action="store_true", help="CI mode: exit 1 on drift")
    parser.add_argument("--max-drift", type=int, default=3, help="Max allowed drift in CI mode")
    parser.add_argument("--compare-run", action="store_true", help="Compare current run against previous --output report")
    args = parser.parse_args()

    import asyncio
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
