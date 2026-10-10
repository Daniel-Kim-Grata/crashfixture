"""Opt-in integration tests. Explicit path required; missing dependencies are errors."""

import json
import sys
from importlib.metadata import version
from pathlib import Path

import pytest
from saver_evidence import CONFIG, inspect_saver, read_checkpoint

from crashfixture import Boundary, Scenario, Status

WORKER = Path(__file__).with_name("worker.py").resolve()


def assess(result):
    assessment = inspect_saver(result)
    (Path(result.artifact_dir) / "saver-result.json").write_text(
        json.dumps({"core": result.to_dict(), "saver": assessment}, indent=2) + "\n"
    )
    return assessment


def run(recovery, boundary, mode="correct", fault_mode="kill", worker=WORKER):
    assert version("langgraph-checkpoint-sqlite") == "3.1.1"
    assert version("langgraph-checkpoint") == "4.1.0"
    result = recovery.run(
        [sys.executable, str(worker), mode],
        Scenario(boundary, timeout=5),
        fault_mode=fault_mode,
    )
    return result, assess(result)


@pytest.mark.parametrize("boundary", list(Boundary))
def test_correct_saver_recovery(recovery, boundary):
    result, assessment = run(recovery, boundary)
    result.require_pass()
    assert result.fault.witnessed
    assert assessment == {"status": "pass", "reasons": []}
    assert len(result.attempts) == 2
    assert result.attempts[0].token != result.attempts[1].token


def test_duplicate_effect_is_a_regression(recovery):
    result, assessment = run(recovery, Boundary.AFTER_EFFECT, "duplicate")
    assert result.fault.witnessed
    assert result.status is Status.FAIL
    assert assessment == {"status": "fail", "reasons": ["duplicate_synthetic_effects"]}


def test_wrong_resume_thread_requires_saver_evidence(recovery):
    result, assessment = run(recovery, Boundary.AFTER_CHECKPOINT, "wrong_thread")
    # Destination idempotency alone cannot establish upstream checkpoint recovery.
    result.require_pass()
    assert assessment == {
        "status": "fail",
        "reasons": ["resume_thread_changed", "recovered_checkpoint_mismatch"],
    }


@pytest.mark.parametrize("boundary", list(Boundary))
def test_noop_fault_is_inconclusive(recovery, boundary):
    result, assessment = run(recovery, boundary, fault_mode="noop")
    assert result.status is Status.INCONCLUSIVE
    assert not result.fault.witnessed
    assert assessment == {"status": "inconclusive", "reasons": ["missing_fault_witness"]}


@pytest.mark.parametrize("damage", ("missing", "malformed", "wrong_token", "invalid_read"))
def test_missing_or_invalid_saver_receipt_is_inconclusive(recovery, damage):
    result, _ = run(recovery, Boundary.AFTER_CHECKPOINT)
    result.require_pass()
    root = Path(result.artifact_dir)
    receipt = root / f"saver-{result.attempts[0].token}-committed.json"
    if damage == "missing":
        receipt.unlink()
    elif damage == "malformed":
        receipt.write_text("{")
    else:
        data = json.loads(receipt.read_text())
        if damage == "wrong_token":
            data["token"] = "unrelated-child"
        else:
            data["read"] = []
        receipt.write_text(json.dumps(data))
    assert assess(result) == {
        "status": "inconclusive",
        "reasons": ["missing_or_invalid_saver_evidence"],
    }


def test_changed_saver_checkpoint_is_a_regression(recovery):
    result, _ = run(recovery, Boundary.AFTER_CHECKPOINT)
    result.require_pass()
    database = Path(result.artifact_dir) / "saver.sqlite3"
    from langgraph.checkpoint.sqlite import SqliteSaver

    with SqliteSaver.from_conn_string(str(database)) as saver:
        saver.delete_thread(CONFIG["configurable"]["thread_id"])
    assert read_checkpoint(database, CONFIG) is None
    assert assess(result) == {
        "status": "fail",
        "reasons": ["final_saver_checkpoint_mismatch"],
    }


def test_unreadable_saver_database_is_inconclusive(recovery):
    result, _ = run(recovery, Boundary.AFTER_CHECKPOINT)
    result.require_pass()
    database = Path(result.artifact_dir) / "saver.sqlite3"
    database.write_bytes(b"not a sqlite database")
    assert assess(result) == {
        "status": "inconclusive",
        "reasons": ["missing_or_invalid_saver_evidence"],
    }


@pytest.mark.parametrize("acknowledgement", ("1", "1.0"))
def test_nonboolean_worker_acknowledgement_is_inconclusive(recovery, tmp_path, acknowledgement):
    # Vary only our original worker's written value; use the real saver and kill.
    worker = tmp_path / "worker.py"
    original = WORKER.read_text()
    assert original.count('"acknowledged": True') == 1
    worker.write_text(
        original.replace('"acknowledged": True', f'"acknowledged": {acknowledgement}')
    )
    (tmp_path / "saver_evidence.py").write_bytes(WORKER.with_name("saver_evidence.py").read_bytes())
    result, assessment = run(recovery, Boundary.AFTER_EFFECT, worker=worker)
    result.require_pass()
    assert result.fault.witnessed
    assert assessment == {
        "status": "inconclusive",
        "reasons": ["missing_or_invalid_saver_evidence"],
    }
