"""Ten actual StateGraph crash cases and three bounded two-process resume cases."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path

from crashfixture import Boundary, Harness, Scenario

PINS = {
    "langgraph": "1.2.14",
    "langgraph-prebuilt": "1.1.0",
    "langgraph-sdk": "0.4.6",
    "langgraph-checkpoint": "4.1.0",
    "langgraph-checkpoint-sqlite": "3.1.1",
}
WORKER = Path(__file__).with_name("graph_worker.py").resolve()


def save(report, root, output):
    for path in (root / "graph-pilot-result.json", output / "latest.json"):
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2) + "\n")
        temporary.replace(path)


def run_cases(report, root, output):
    report["versions"] = {name: version(name) for name in PINS}
    if report["versions"] != PINS:
        raise RuntimeError(f"graph pilot requires exact optional versions: {PINS}")
    from concurrent_graph import run as concurrent
    from graph_evidence import inspect_graph

    cases = [
        (mode, boundary, "kill")
        for mode in ("unsafe", "stable_key", "no_lookup")
        for boundary in Boundary
    ]
    cases.append(("stable_key", Boundary.AFTER_CHECKPOINT, "noop"))
    for mode, boundary, fault_mode in cases:
        name = f"{mode}.{boundary.value}.{fault_mode}"
        hold = mode == "no_lookup" and boundary is not Boundary.AFTER_CHECKPOINT
        effects = 0 if hold and boundary is Boundary.BEFORE_DISPATCH else 1
        broken = mode == "unsafe" and boundary is Boundary.AFTER_EFFECT
        expected = "inconclusive" if fault_mode == "noop" else "fail" if broken else "pass"
        reasons = (
            {"missing_fault_witness"}
            if fault_mode == "noop"
            else {"duplicate_synthetic_effects", "provider_effect_count"}
            if broken
            else set()
        )
        result = Harness(root / name).run(
            [sys.executable, str(WORKER), mode],
            Scenario(
                boundary,
                max_attempts=2,
                timeout=6,
                expected_effects=effects,
                expected_terminal="failed" if hold else "succeeded",
            ),
            fault_mode=fault_mode,
        )
        assessment = inspect_graph(result, mode)
        checks = {
            "core_verdict": result.status.value == expected,
            "graph_verdict": assessment["status"] == expected,
            "graph_reasons": set(assessment["reasons"]) == reasons,
            "fault_delivery": bool(result.fault and result.fault.witnessed)
            == (fault_mode == "kill"),
            "fresh_launches": len(result.attempts) == (1 if fault_mode == "noop" else 2)
            and len({attempt.token for attempt in result.attempts}) == len(result.attempts),
            "effect_count": len(result.final_snapshot.get("effects", []))
            == (2 if broken else effects),
        }
        case = {
            "name": name,
            "core": result.to_dict(),
            "graph": assessment,
            "checks": checks,
            "expectations_met": all(checks.values()),
        }
        (Path(result.artifact_dir) / "graph-result.json").write_text(
            json.dumps(case, indent=2) + "\n"
        )
        report["graph_cases"].append(case)
        save(report, root, output)
        print(f"{name}: core={result.status.value}, graph={assessment['status']}")

    for mode, guarded in (("unsafe", False), ("stable_key", True), ("no_lookup", True)):
        result = concurrent(root / f"concurrent.{mode}", mode, guarded)
        expected = "fail" if mode == "unsafe" else "pass"
        reasons = (
            {"provider_effect_count", "duplicate_synthetic_effects"} if mode == "unsafe" else set()
        )
        checks = {
            "concurrent_verdict": result["status"] == expected,
            "concurrent_reasons": set(result["reasons"]) == reasons,
            "effect_count": len(result.get("provider_effects", []))
            == (2 if mode == "unsafe" else 1),
            "lock_witness": result.get("lock_contention_witness") is guarded,
            "two_resumes": len(result.get("launches", [])) == 2,
        }
        result.update(checks=checks, expectations_met=all(checks.values()))
        report["concurrent_cases"].append(result)
        save(report, root, output)
        witness = result.get("lock_contention_witness")
        print(f"concurrent.{mode}: {result['status']}, lock_witness={witness}")
    report["calibration_passed"] = (
        len(report["graph_cases"]) == 10
        and len(report["concurrent_cases"]) == 3
        and all(
            case["expectations_met"] for case in report["graph_cases"] + report["concurrent_cases"]
        )
    )
    report["status"] = "complete"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/graph-pilot"))
    output = parser.parse_args(argv).output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="run-", dir=output))
    report = {
        "schema_version": 1,
        "status": "running",
        "calibration_passed": False,
        "run_dir": str(root),
        "graph_cases": [],
        "concurrent_cases": [],
    }
    save(report, root, output)
    try:
        run_cases(report, root, output)
    except Exception as error:
        report.update(
            status="error", calibration_passed=False, error=f"{type(error).__name__}: {error}"
        )
        save(report, root, output)
        print(report["error"], file=sys.stderr)
        return 2
    save(report, root, output)
    print(f"calibration_passed={report['calibration_passed']}; evidence: {output / 'latest.json'}")
    return 0 if report["calibration_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
