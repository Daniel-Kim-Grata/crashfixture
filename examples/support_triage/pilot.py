"""Offline synthetic support-triage pilot; expected failures calibrate the fixture."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path

from crashfixture import Boundary, Harness, Ledger, Scenario

PINS = {"langgraph-checkpoint-sqlite": "3.1.1", "langgraph-checkpoint": "4.1.0"}
WORKER = Path(__file__).with_name("worker.py").resolve()


def cases():
    for mode in ("unsafe", "stable_key", "no_lookup"):
        for boundary in Boundary:
            yield mode, boundary, "kill"
    yield "stalled", Boundary.AFTER_CHECKPOINT, "kill"
    yield "stable_key", Boundary.AFTER_CHECKPOINT, "noop"


def save(report, root, output):
    for path in (root / "pilot-result.json", output / "latest.json"):
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2) + "\n")
        temporary.replace(path)


def duplicate_resume(root, mode):
    """Two sequential fresh terminal resumes; no concurrent-owner safety claim."""
    from worker import provider_snapshot, read_saver, require_identity

    before = {
        "provider": provider_snapshot(root),
        "saver": read_saver(root),
        "ledger": Ledger(root / "ledger.sqlite3").snapshot(),
        "visible": json.loads((root / "workflow-status.json").read_text()),
    }
    require_identity(before["saver"])
    if before["saver"] is None or before["saver"]["channels"]["phase"] not in {
        "acknowledged",
        "reconciliation_hold",
    }:
        raise ValueError("duplicate resume requires a valid terminal or hold checkpoint")
    launches = []
    for _ in range(2):
        previous = set(root.glob("triage-*-replay.json"))
        with subprocess.Popen(
            [sys.executable, str(WORKER), mode, "--replay", str(root)],
            cwd=root,
            env={"PATH": os.defpath, "HOME": str(root / "home"), "TMPDIR": str(root / "tmp")},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        ) as child:
            try:
                code = child.wait(timeout=5)
            finally:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait(timeout=1)
        new = set(root.glob("triage-*-replay.json")) - previous
        if code != 0 or len(new) != 1:
            raise RuntimeError("duplicate resume did not produce one successful child receipt")
        path = new.pop()
        receipt = json.loads(path.read_text())
        if (
            set(receipt) != {"schema_version", "pid", "token", "attempt", "read"}
            or type(receipt["schema_version"]) is not int
            or receipt["schema_version"] != 1
            or type(receipt["pid"]) is not int
            or receipt["pid"] != child.pid
            or type(receipt["attempt"]) is not int
            or receipt["attempt"] != 0
            or not isinstance(receipt["token"], str)
            or not receipt["token"]
            or path.name != f"triage-{receipt['token']}-replay.json"
            or receipt["read"] != before["saver"]
        ):
            raise ValueError("invalid duplicate-resume child evidence")
        launches.append({"pid": child.pid, "token": receipt["token"], "returncode": code})
    after = {
        "provider": provider_snapshot(root),
        "saver": read_saver(root),
        "ledger": Ledger(root / "ledger.sqlite3").snapshot(),
        "visible": json.loads((root / "workflow-status.json").read_text()),
    }
    return {
        "mode": mode,
        "artifact_dir": str(root),
        "launches": launches,
        "expectations_met": before == after and len({launch["token"] for launch in launches}) == 2,
        "before": before,
        "after": after,
    }


def run_cases(report, root, output):
    report["versions"] = {name: version(name) for name in PINS}
    if report["versions"] != PINS:
        raise RuntimeError(f"pilot requires exact optional versions: {PINS}")
    from triage_evidence import inspect_workflow

    for mode, boundary, fault_mode in cases():
        name = f"{mode}.{boundary.value}.{fault_mode}"
        hold = mode == "no_lookup" and boundary is not Boundary.AFTER_CHECKPOINT
        effects = 0 if hold and boundary is Boundary.BEFORE_DISPATCH else 1
        broken = mode == "unsafe" and boundary is Boundary.AFTER_EFFECT
        stalled = mode == "stalled"
        expected = (
            "inconclusive" if fault_mode == "noop" else "fail" if broken or stalled else "pass"
        )
        reasons = (
            {"missing_fault_witness"}
            if fault_mode == "noop"
            else {"duplicate_synthetic_effects", "provider_effect_count"}
            if broken
            else {
                "recovery_timeout",
                "nonzero_terminal_exit",
                "terminal_outcome",
                "visible_stalled_run",
            }
            if stalled
            else set()
        )
        result = Harness(root / name).run(
            [sys.executable, str(WORKER), mode],
            Scenario(
                boundary,
                max_attempts=2,
                timeout=2,
                expected_effects=effects,
                expected_terminal="failed" if hold else "succeeded",
            ),
            fault_mode=fault_mode,
        )
        assessment = inspect_workflow(result, mode)
        checks = {
            "core_verdict": result.status.value == expected,
            "workflow_verdict": assessment["status"] == expected,
            "workflow_reasons": set(assessment["reasons"]) == reasons,
            "fault_delivery": bool(result.fault and result.fault.witnessed)
            == (fault_mode == "kill"),
            "launch_count": len(result.attempts) == (1 if fault_mode == "noop" else 2),
            "fresh_tokens": len({attempt.token for attempt in result.attempts})
            == len(result.attempts),
            "effect_count": len(result.final_snapshot.get("effects", []))
            == (2 if broken else effects),
        }
        if fault_mode != "noop":
            checks["visible_phase"] = assessment.get("workflow_phase") == (
                "stalled" if stalled else "reconciliation_hold" if hold else "acknowledged"
            )
            checks["business_completion"] = assessment.get("business_complete") is (
                not hold and not stalled
            )
        case = {
            "name": name,
            "core": result.to_dict(),
            "workflow": assessment,
            "checks": checks,
            "expectations_met": all(checks.values()),
        }
        (Path(result.artifact_dir) / "workflow-result.json").write_text(
            json.dumps(case, indent=2) + "\n"
        )
        report["cases"].append(case)
        if (
            name in {"stable_key.after_checkpoint.kill", "no_lookup.after_effect.kill"}
            and case["expectations_met"]
        ):
            report["duplicate_resumes"].append(duplicate_resume(Path(result.artifact_dir), mode))
        save(report, root, output)
        print(
            f"{name}: core={result.status.value}, workflow={assessment['status']}, "
            f"phase={assessment.get('workflow_phase', 'unproven')}"
        )
    report["calibration_passed"] = (
        len(report["cases"]) == 11
        and all(case["expectations_met"] for case in report["cases"])
        and len(report["duplicate_resumes"]) == 2
        and all(replay["expectations_met"] for replay in report["duplicate_resumes"])
    )
    report["status"] = "complete"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/support-triage"))
    output = parser.parse_args(argv).output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="run-", dir=output))
    report = {
        "schema_version": 1,
        "status": "running",
        "calibration_passed": False,
        "run_dir": str(root),
        "cases": [],
        "duplicate_resumes": [],
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
