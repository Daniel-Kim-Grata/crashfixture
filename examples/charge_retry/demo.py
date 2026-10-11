"""Calibrate seven original synthetic charge controls; never a payment integration."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

from worker import AMOUNT_CENTS, OPERATION, charges

from crashfixture import Boundary, Harness, Scenario, Status

WORKER = Path(__file__).with_name("worker.py").resolve()


def inspect_charge(result, mode):
    if result.status is Status.INCONCLUSIVE:
        return {"status": "inconclusive", "reasons": list(result.reasons)}
    try:
        rows = charges(Path(result.artifact_dir))
        if any(
            set(row) != {"charge_id", "request_key", "operation", "amount_cents"}
            or type(row["charge_id"]) is not int
            or row["charge_id"] <= 0
            or not isinstance(row["request_key"], str)
            or not row["request_key"]
            or not isinstance(row["operation"], str)
            or type(row["amount_cents"]) is not int
            for row in rows
        ):
            raise ValueError("malformed synthetic charge rows")
    except (OSError, ValueError, sqlite3.Error):
        return {"status": "inconclusive", "reasons": ["missing_or_invalid_charge_evidence"]}
    reasons = list(result.reasons)
    if len(rows) != 1:
        reasons.append("synthetic_charge_count")
    if any(row["operation"] != OPERATION or row["amount_cents"] != AMOUNT_CENTS for row in rows):
        reasons.append("unexpected_synthetic_charge")
    if {row["request_key"] for row in rows} != {
        row["effect_key"] for row in result.final_snapshot["effects"]
    }:
        reasons.append("charge_mirror_mismatch")
    if mode == "stable_key" and any(row["request_key"] != OPERATION for row in rows):
        reasons.append("unstable_charge_key")
    return {
        "status": "fail" if reasons else "pass",
        "reasons": list(dict.fromkeys(reasons)),
        "charge_count": len(rows),
        "total_cents": sum(row["amount_cents"] for row in rows),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/charge-retry"))
    output = parser.parse_args(argv).output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="run-", dir=output))
    report = {"schema_version": 1, "synthetic": True, "calibration_passed": False, "cases": []}
    path = output / "latest.json"

    def save():
        for destination in (path, root / "charge-result.json"):
            temporary = destination.with_suffix(".tmp")
            temporary.write_text(json.dumps(report, indent=2) + "\n")
            temporary.replace(destination)

    save()
    try:
        cases = [
            (mode, boundary, "kill") for mode in ("unsafe", "stable_key") for boundary in Boundary
        ]
        cases.append(("stable_key", Boundary.AFTER_EFFECT, "noop"))
        for mode, boundary, fault in cases:
            name = f"{mode}.{boundary.value}.{fault}"
            broken = mode == "unsafe" and boundary is Boundary.AFTER_EFFECT
            expected = "inconclusive" if fault == "noop" else "fail" if broken else "pass"
            expected_reasons = (
                {"missing_fault_witness"}
                if fault == "noop"
                else {"duplicate_synthetic_effects", "synthetic_charge_count"}
                if broken
                else set()
            )
            result = Harness(root / name).run(
                [sys.executable, str(WORKER), mode],
                Scenario(boundary, max_attempts=2),
                fault_mode=fault,
            )
            assessment = inspect_charge(result, mode)
            checks = {
                "core_verdict": result.status.value == expected,
                "charge_verdict": assessment["status"] == expected,
                "reasons": set(assessment["reasons"]) == expected_reasons,
                "fault_witness": bool(result.fault and result.fault.witnessed) == (fault == "kill"),
                "fresh_launches": len(result.attempts) == (2 if fault == "kill" else 1)
                and len({attempt.token for attempt in result.attempts}) == len(result.attempts),
                "physical_charge_count": len(charges(Path(result.artifact_dir)))
                == (2 if broken else 1),
            }
            report["cases"].append(
                {"name": name, "core": result.to_dict(), "charge": assessment, "checks": checks}
            )
            save()
            print(f"{name}: {assessment['status']}")
        report["calibration_passed"] = len(report["cases"]) == 7 and all(
            all(case["checks"].values()) for case in report["cases"]
        )
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        save()
        print(report["error"], file=sys.stderr)
        return 2
    save()
    print(f"calibration_passed={report['calibration_passed']}; evidence: {path}")
    return 0 if report["calibration_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
