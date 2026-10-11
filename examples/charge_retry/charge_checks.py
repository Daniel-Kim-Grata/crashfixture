"""Opt-in physical-charge evidence checks for an original SQLite model."""

import json
import sqlite3
import sys
from pathlib import Path

import demo
import pytest

from crashfixture import Boundary, Scenario


def test_synthetic_charge_calibration(tmp_path):
    assert demo.main(["--output", str(tmp_path)]) == 0
    report = json.loads((tmp_path / "latest.json").read_text())
    assert report["synthetic"] is True and report["calibration_passed"] is True
    cases = {case["name"]: case for case in report["cases"]}
    broken = cases["unsafe.after_effect.kill"]
    assert broken["charge"]["status"] == "fail"
    assert broken["charge"]["charge_count"] == 2 and broken["charge"]["total_cents"] == 1400
    safe = cases["stable_key.after_effect.kill"]
    assert safe["charge"]["status"] == "pass" and safe["charge"]["total_cents"] == 700
    assert cases["stable_key.after_effect.noop"]["charge"]["status"] == "inconclusive"
    killed = [
        case
        for case in report["cases"]
        if case["core"]["fault"]["sent_signal"] == 9
        and case["core"]["fault"]["wait_returncode"] == -9
        and case["core"]["fault"]["snapshot_valid"] is True
    ]
    assert len(killed) == 6
    assert all(case["core"]["fault"]["wait_returncode"] == -9 for case in killed)


@pytest.mark.parametrize("damage", ("missing", "amount", "operation", "mirror"))
def test_core_pass_cannot_hide_damaged_physical_charge(recovery, damage):
    result = recovery.run(
        [sys.executable, str(demo.WORKER), "stable_key"], Scenario(Boundary.AFTER_EFFECT)
    )
    result.require_pass()
    assert demo.inspect_charge(result, "stable_key")["status"] == "pass"
    path = Path(result.artifact_dir) / "charges.sqlite3"
    if damage == "missing":
        path.unlink()
    else:
        column, value = {
            "amount": ("amount_cents", 701),
            "operation": ("operation", "other-synthetic-operation"),
            "mirror": ("request_key", "other-synthetic-key"),
        }[damage]
        with sqlite3.connect(path) as connection:
            connection.execute(f"UPDATE charges SET {column} = ?", (value,))
    assessment = demo.inspect_charge(result, "stable_key")
    assert assessment["status"] == ("inconclusive" if damage == "missing" else "fail")
    expected = (
        "missing_or_invalid_charge_evidence"
        if damage == "missing"
        else "charge_mirror_mismatch"
        if damage == "mirror"
        else "unexpected_synthetic_charge"
    )
    assert expected in assessment["reasons"]
