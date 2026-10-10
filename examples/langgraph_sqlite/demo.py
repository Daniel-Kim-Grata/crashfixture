"""Offline application-fixture calibration; exit zero only for expected controls."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path

from crashfixture import Boundary, Harness, Scenario

PINS = {"langgraph-checkpoint-sqlite": "3.1.1", "langgraph-checkpoint": "4.1.0"}
WORKER = Path(__file__).with_name("worker.py").resolve()
CASES = (
    ("correct", Boundary.AFTER_CHECKPOINT, "correct", "kill", "pass", "pass", ()),
    (
        "wrong_thread",
        Boundary.AFTER_CHECKPOINT,
        "wrong_thread",
        "kill",
        "pass",
        "fail",
        ("resume_thread_changed", "recovered_checkpoint_mismatch"),
    ),
    (
        "duplicate",
        Boundary.AFTER_EFFECT,
        "duplicate",
        "kill",
        "fail",
        "fail",
        ("duplicate_synthetic_effects",),
    ),
    (
        "noop",
        Boundary.AFTER_CHECKPOINT,
        "correct",
        "noop",
        "inconclusive",
        "inconclusive",
        ("missing_fault_witness",),
    ),
)


def save(report, root, output):
    for path in (root / "demo-result.json", output / "latest.json"):
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2) + "\n")
        temporary.replace(path)


def run_cases(report, root, output):
    report["versions"] = {package: version(package) for package in PINS}
    if report["versions"] != PINS:
        raise RuntimeError(f"demo requires exact optional versions: {PINS}")
    from saver_evidence import inspect_saver

    for name, boundary, mode, fault_mode, core_status, saver_status, reasons in CASES:
        result = Harness(root / name).run(
            [sys.executable, str(WORKER), mode],
            Scenario(boundary, max_attempts=2, timeout=5),
            fault_mode=fault_mode,
        )
        assessment = inspect_saver(result)
        killed = fault_mode == "kill"
        expected_effects = 2 if mode == "duplicate" else 1
        checks = {
            "core_verdict": result.status.value == core_status,
            "saver_verdict": assessment == {"status": saver_status, "reasons": list(reasons)},
            "fault_delivery": bool(result.fault and result.fault.witnessed) == killed,
            "effect_count": len(result.final_snapshot.get("effects", [])) == expected_effects,
            "launch_count": len(result.attempts) == (2 if killed else 1),
            "fresh_tokens": len({attempt.token for attempt in result.attempts})
            == len(result.attempts),
            "terminal_exit": result.attempts[-1].returncode == 0,
        }
        case = {
            "name": name,
            "core": result.to_dict(),
            "saver": assessment,
            "checks": checks,
            "expectations_met": all(checks.values()),
        }
        (Path(result.artifact_dir) / "saver-result.json").write_text(
            json.dumps(case, indent=2) + "\n"
        )
        report["cases"].append(case)
        save(report, root, output)
        print(f"{name}: core={result.status.value}, saver={assessment['status']}")
    report["calibration_passed"] = all(case["expectations_met"] for case in report["cases"])
    report["status"] = "complete"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/saver-demo"))
    args = parser.parse_args(argv)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="run-", dir=output))
    report = {
        "schema_version": 1,
        "status": "running",
        "calibration_passed": False,
        "run_dir": str(root),
        "cases": [],
    }
    save(report, root, output)  # Replace any earlier success before preflight or dispatch.
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
