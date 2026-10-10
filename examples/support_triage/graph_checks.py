"""Opt-in real graph and bounded concurrent-resume evidence regressions."""

import json
import subprocess
import sys
from pathlib import Path

import concurrent_graph
import graph_pilot
import pytest
from graph_evidence import inspect_graph

from crashfixture import Boundary, Scenario


def test_full_graph_and_concurrent_calibration(tmp_path):
    assert graph_pilot.main(["--output", str(tmp_path)]) == 0
    report = json.loads((tmp_path / "latest.json").read_text())
    assert report["calibration_passed"] is True
    assert len(report["graph_cases"]) == 10
    assert len(report["concurrent_cases"]) == 3
    cases = {case["name"]: case for case in report["graph_cases"]}
    assert cases["unsafe.after_effect.kill"]["graph"]["status"] == "fail"
    assert cases["stable_key.after_effect.kill"]["graph"]["status"] == "pass"
    hold = cases["no_lookup.after_effect.kill"]["graph"]
    assert hold["status"] == "pass" and hold["business_complete"] is False
    assert cases["stable_key.after_checkpoint.noop"]["graph"]["status"] == "inconclusive"
    concurrent = {case["mode"]: case for case in report["concurrent_cases"]}
    assert concurrent["unsafe"]["status"] == "fail"
    assert len(concurrent["unsafe"]["provider_effects"]) == 2
    assert concurrent["stable_key"]["status"] == "pass"
    assert len(concurrent["stable_key"]["provider_effects"]) == 1
    assert concurrent["no_lookup"]["visible_status"]["business_complete"] is False
    assert concurrent["no_lookup"]["visible_status"]["automatic_retry_allowed"] is False
    assert sum(case["lock_contention_witness"] for case in report["concurrent_cases"]) == 2
    kills = [case["core"] for case in report["graph_cases"] if case["core"]["fault"]]
    kills += [case["seed_core"] for case in report["concurrent_cases"]]
    assert (
        sum(
            case["fault"]["sent_signal"] == 9 and case["fault"]["wait_returncode"] == -9
            for case in kills
        )
        == 12
    )
    assert (
        sum(len(case["core"]["attempts"]) for case in report["graph_cases"])
        + sum(
            len(case["seed_core"]["attempts"]) + len(case["launches"])
            for case in report["concurrent_cases"]
        )
        == 28
    )


@pytest.mark.parametrize("damage", ("wrong_scope", "task_error", "missing_done", "integer_status"))
def test_graph_parent_gate_rejects_damaged_evidence(recovery, damage):
    result = recovery.run(
        [sys.executable, str(graph_pilot.WORKER), "stable_key"],
        Scenario(Boundary.AFTER_EFFECT, max_attempts=2, timeout=6),
    )
    result.require_pass()
    assert inspect_graph(result, "stable_key")["status"] == "pass"
    root = Path(result.artifact_dir)
    first, second = result.attempts
    if damage in {"wrong_scope", "task_error"}:
        for token, stage in ((first.token, "intent"), (second.token, "start")):
            path = root / f"graph-{token}-{stage}.json"
            value = json.loads(path.read_text())
            if damage == "wrong_scope":
                value["read"]["config"].update(thread_id="other-ticket", checkpoint_ns="other")
            else:
                value["read"]["task_errors"] = ["synthetic task error"]
            path.write_text(json.dumps(value))
    elif damage == "missing_done":
        (root / f"graph-{second.token}-done.json").unlink()
    else:
        path = root / "workflow-status.json"
        value = json.loads(path.read_text())
        value["business_complete"] = 1
        path.write_text(json.dumps(value))
    assessment = inspect_graph(result, "stable_key")
    if damage in {"wrong_scope", "task_error"}:
        assert assessment["status"] == "fail"
        assert assessment["reasons"] == ["unexpected_graph_boundary"]
    else:
        assert assessment == {
            "status": "inconclusive",
            "reasons": ["missing_or_invalid_graph_evidence"],
        }


@pytest.mark.parametrize(
    "damage", ("unobserved_writer", "wrong_start", "wrong_ticket", "lost_action", "integer_status")
)
def test_concurrent_gate_rejects_damaged_evidence(tmp_path, monkeypatch, damage):
    real_resumes = concurrent_graph.resumes

    def damaged_resumes(root, mode, guarded):
        launches, witness = real_resumes(root, mode, guarded)
        blocked = next(item for item in launches if item["events"][-1]["stage"] == "blocked")
        if damage in {"unobserved_writer", "wrong_start"}:
            stage = "done" if damage == "unobserved_writer" else "start"
            path = root / f"graph-{blocked['token']}-{stage}.json"
            value = json.loads(path.read_text())
            if damage == "unobserved_writer":
                value["read"]["writer_token"] = "unobserved-synthetic-worker"
            else:
                value["read"]["config"]["checkpoint_id"] = "other-synthetic-checkpoint"
        else:
            path = root / "workflow-status.json"
            value = json.loads(path.read_text())
            if damage == "wrong_ticket":
                value["ticket_id"] = "other-ticket"
            elif damage == "lost_action":
                value["action_required"] = None
            else:
                value["automatic_retry_allowed"] = 0
        path.write_text(json.dumps(value))
        return launches, witness

    monkeypatch.setattr(concurrent_graph, "resumes", damaged_resumes)
    result = concurrent_graph.run(tmp_path, "no_lookup", True)
    if damage == "integer_status":
        assert result["status"] == "inconclusive"
    else:
        assert result["status"] == "fail"
        reason = (
            "unobserved_child_graph_writer"
            if damage == "unobserved_writer"
            else "guarded_contender_checkpoint_mismatch"
            if damage == "wrong_start"
            else "concurrent_visible_status"
        )
        assert reason in result["reasons"]


def test_claimed_lock_without_physical_witness_is_inconclusive(tmp_path, monkeypatch):
    # Parent observation only: fresh child interpreters still use real fcntl.
    monkeypatch.setattr(concurrent_graph.fcntl, "flock", lambda *_: None)
    result = concurrent_graph.run(tmp_path, "stable_key", True)
    assert result["status"] == "inconclusive"
    assert result["reasons"] == ["missing_or_invalid_concurrent_evidence"]
    assert "parent could acquire claimed owned lock" in result["detail"]


def test_cleanup_attempts_all_owned_groups_and_descriptors(monkeypatch):
    calls = []

    class Process:
        def __init__(self, pid):
            self.pid = pid

        def wait(self, timeout):
            calls.append(("wait", self.pid))
            if self.pid == 100001:
                raise subprocess.TimeoutExpired("mock-child", timeout)

    monkeypatch.setattr(concurrent_graph.os, "killpg", lambda pid, _: calls.append(("kill", pid)))
    monkeypatch.setattr(concurrent_graph.os, "close", lambda fd: calls.append(("close", fd)))
    children = [
        {"process": Process(100001), "read": 101, "write": 102},
        {"process": Process(100002), "read": 103, "write": 104},
    ]
    with pytest.raises(RuntimeError, match="cleanup incomplete.*reap child 100001"):
        concurrent_graph.cleanup(children)
    assert calls == [
        ("kill", 100001),
        ("wait", 100001),
        ("close", 101),
        ("close", 102),
        ("kill", 100002),
        ("wait", 100002),
        ("close", 103),
        ("close", 104),
    ]
