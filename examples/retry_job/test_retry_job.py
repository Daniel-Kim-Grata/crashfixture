"""Keep the duplicate-output crash regression in a queued job's CI."""

import signal
import sys
from pathlib import Path

import pytest

from crashfixture import Boundary, InconclusiveError, Scenario, Status

WORKER = Path(__file__).with_name("worker.py").resolve()


def test_unsafe_attempt_key_duplicates_committed_output(recovery):
    result = recovery.run([sys.executable, str(WORKER), "unsafe"], Scenario(Boundary.AFTER_EFFECT))
    assert result.fault.witnessed
    assert result.status is Status.FAIL
    assert "duplicate_synthetic_effects" in result.reasons
    assert len(result.final_snapshot["effects"]) == 2
    with pytest.raises(AssertionError, match="duplicate_synthetic_effects"):
        result.require_pass()
    print("unsafe after_effect: fail; 2 committed output rows")


@pytest.mark.parametrize("boundary", list(Boundary))
def test_stable_job_key_recovers_one_output(recovery, boundary):
    result = recovery.run(
        [sys.executable, str(WORKER), "stable_key"], Scenario(boundary)
    ).require_pass()
    assert result.fault.witnessed
    assert [attempt.returncode for attempt in result.attempts] == [-signal.SIGKILL, 0]
    assert result.attempts[0].pid != result.attempts[1].pid
    assert len(result.final_snapshot["effects"]) == 1
    assert result.final_snapshot["effects"][0]["effect_key"] == "report:weekly-report-001"
    assert result.final_snapshot["tasks"][0]["checkpoint"] == 1
    assert result.final_snapshot["tasks"][0]["state"] == "succeeded"
    print(f"stable_key {boundary.value}: pass; 1 committed output row")


def test_noop_cannot_certify_crash_recovery(recovery):
    result = recovery.run(
        [sys.executable, str(WORKER), "stable_key"],
        Scenario(Boundary.AFTER_EFFECT),
        fault_mode="noop",
    )
    assert result.status is Status.INCONCLUSIVE
    assert "missing_fault_witness" in result.reasons
    with pytest.raises(InconclusiveError):
        result.require_pass()
    print("stable_key noop: inconclusive; missing fault witness")
