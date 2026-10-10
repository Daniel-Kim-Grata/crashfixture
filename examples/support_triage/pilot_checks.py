"""Opt-in integration checks; same optional pins as the saver example."""

import json
import sqlite3
import sys
from pathlib import Path

import pilot
import pytest
from triage_evidence import inspect_workflow

from crashfixture import Boundary, Scenario, Status


def test_full_pilot_and_sequential_duplicate_resumes(tmp_path):
    assert pilot.main(["--output", str(tmp_path)]) == 0
    report = json.loads((tmp_path / "latest.json").read_text())
    assert report["calibration_passed"] is True
    assert len(report["cases"]) == 11
    cases = {case["name"]: case for case in report["cases"]}
    unsafe = cases["unsafe.after_effect.kill"]
    corrected = cases["stable_key.after_effect.kill"]
    hold = cases["no_lookup.after_effect.kill"]
    assert unsafe["workflow"]["provider_effects"] == 2
    assert unsafe["workflow"]["status"] == "fail"
    assert corrected["workflow"]["provider_effects"] == 1
    assert corrected["workflow"]["status"] == "pass"
    assert hold["workflow"]["provider_effects"] == 1
    assert hold["workflow"]["workflow_phase"] == "reconciliation_hold"
    assert hold["workflow"]["business_complete"] is False
    assert cases["no_lookup.before_dispatch.kill"]["workflow"]["provider_effects"] == 0
    assert cases["stalled.after_checkpoint.kill"]["workflow"]["workflow_phase"] == "stalled"
    assert len(report["duplicate_resumes"]) == 2
    assert all(replay["expectations_met"] for replay in report["duplicate_resumes"])
    assert sum(case["core"]["fault"]["sent_signal"] == 9 for case in report["cases"]) == 10


@pytest.mark.parametrize("damage", ("missing_receipt", "provider_route", "integer_completion"))
def test_parent_evidence_cannot_silently_pass(recovery, damage):
    result = recovery.run(
        [sys.executable, str(pilot.WORKER), "stable_key"],
        Scenario(Boundary.AFTER_CHECKPOINT, max_attempts=2, timeout=2),
    )
    result.require_pass()
    assert inspect_workflow(result, "stable_key")["status"] == "pass"
    root = Path(result.artifact_dir)
    if damage == "missing_receipt":
        (root / f"triage-{result.attempts[0].token}-committed.json").unlink()
    elif damage == "provider_route":
        with sqlite3.connect(root / "provider.sqlite3") as connection:
            connection.execute("UPDATE effects SET route='unexpected-synthetic-route'")
    else:
        path = root / "workflow-status.json"
        value = json.loads(path.read_text())
        value["business_complete"] = 1
        path.write_text(json.dumps(value))
    assessment = inspect_workflow(result, "stable_key")
    if damage == "provider_route":
        assert assessment["status"] == "fail"
        assert assessment["reasons"] == ["unexpected_provider_effect"]
    else:
        assert assessment == {
            "status": "inconclusive",
            "reasons": ["missing_or_invalid_workflow_evidence"],
        }


def test_absent_fault_witness_stays_inconclusive(recovery):
    result = recovery.run(
        [sys.executable, str(pilot.WORKER), "stable_key"],
        Scenario(Boundary.AFTER_CHECKPOINT, max_attempts=2, timeout=2),
        fault_mode="noop",
    )
    assert result.status is Status.INCONCLUSIVE
    assert inspect_workflow(result, "stable_key") == {
        "status": "inconclusive",
        "reasons": ["missing_fault_witness"],
    }


def test_wrong_saved_business_identity_blocks_duplicate_resume(recovery):
    from langgraph.checkpoint.base import empty_checkpoint
    from langgraph.checkpoint.sqlite import SqliteSaver
    from worker import CONFIG, provider_snapshot

    result = recovery.run(
        [sys.executable, str(pilot.WORKER), "stable_key"],
        Scenario(Boundary.AFTER_CHECKPOINT, max_attempts=2, timeout=2),
    )
    result.require_pass()
    root = Path(result.artifact_dir)
    before = provider_snapshot(root)
    with SqliteSaver.from_conn_string(str(root / "saver.sqlite3")) as saver:
        saved = saver.get_tuple(CONFIG)
        value = empty_checkpoint()
        value["channel_values"] = {
            **saved.checkpoint["channel_values"],
            "business_key": "other-synthetic-operation",
        }
        value["channel_versions"] = saved.checkpoint["channel_versions"]
        saver.put(CONFIG, value, saved.metadata, value["channel_versions"])
    assert inspect_workflow(result, "stable_key")["status"] == "fail"
    with pytest.raises(ValueError, match="checkpoint identity"):
        pilot.duplicate_resume(root, "stable_key")
    assert provider_snapshot(root) == before
    assert not list(root.glob("triage-*-replay.json"))
