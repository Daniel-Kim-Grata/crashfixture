from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from crashfixture import Boundary, Harness, InconclusiveError, Scenario, Status
from crashfixture.cli import worker_command
from crashfixture.harness import _evaluate


def custom_worker(tmp_path: Path, source: str) -> list[str]:
    path = tmp_path / "synthetic_worker.py"
    path.write_text(source, encoding="utf-8")
    return [sys.executable, str(path)]


@pytest.mark.parametrize("boundary", list(Boundary))
def test_real_crash_committed_state_and_fresh_process(recovery: Harness, boundary: Boundary):
    result = recovery.run(worker_command("correct"), Scenario(boundary)).require_pass()
    assert len(result.attempts) == 2
    old, new = result.attempts
    assert old.returncode == -signal.SIGKILL
    assert new.returncode == 0
    assert old.token != new.token
    assert result.fault.pid == old.pid
    assert result.fault.token == old.token
    assert result.fault.witnessed
    assert len(result.fault.snapshot["effects"]) == (boundary is not Boundary.BEFORE_DISPATCH)
    assert result.fault.snapshot["tasks"][0]["checkpoint"] == (
        boundary is Boundary.AFTER_CHECKPOINT
    )
    saved = json.loads((Path(result.artifact_dir) / "fault.json").read_text())
    assert saved["wait_returncode"] == -9
    assert result.final_snapshot["tasks"][0]["state"] == "succeeded"
    assert result.final_snapshot["tasks"][0]["attempts"] == 2
    assert len(result.final_snapshot["effects"]) == 1


@pytest.mark.parametrize(
    ("worker", "boundary", "reason"),
    [
        ("duplicate", Boundary.AFTER_EFFECT, "duplicate_synthetic_effects"),
        ("forget_checkpoint", Boundary.AFTER_CHECKPOINT, "duplicate_synthetic_effects"),
        ("lose_task", Boundary.BEFORE_DISPATCH, "missing_synthetic_effect"),
        ("lose_task", Boundary.AFTER_EFFECT, "checkpoint_state"),
        ("reset_attempts", Boundary.BEFORE_DISPATCH, "attempt_accounting"),
        ("retry_forever", Boundary.BEFORE_DISPATCH, "terminal_outcome"),
    ],
)
def test_broken_workers_fail_with_a_real_witness(recovery, worker, boundary, reason):
    result = recovery.run(worker_command(worker), Scenario(boundary))
    assert result.status is Status.FAIL
    assert result.fault.witnessed
    assert reason in result.reasons
    with pytest.raises(AssertionError, match=reason):
        result.require_pass()


def test_correct_retry_budget_and_terminal_failure(recovery):
    scenario = Scenario(
        Boundary.BEFORE_DISPATCH, max_attempts=3, expected_terminal="failed", expected_effects=0
    )
    result = recovery.run(worker_command("retry"), scenario).require_pass()
    assert [a.returncode for a in result.attempts] == [-9, 75, 0]
    assert result.final_snapshot["tasks"][0]["attempts"] == 3
    assert result.final_snapshot["tasks"][0]["state"] == "failed"
    assert not result.final_snapshot["effects"]


def test_retry_forever_is_capped_by_the_harness(recovery):
    result = recovery.run(worker_command("retry_forever"), Scenario(Boundary.BEFORE_DISPATCH))
    assert result.status is Status.FAIL
    assert len(result.attempts) == 3
    assert [a.returncode for a in result.attempts] == [-9, 75, 75]


def test_one_launch_cannot_claim_fresh_process_recovery(recovery):
    result = recovery.run(
        worker_command("correct"), Scenario(Boundary.AFTER_CHECKPOINT, max_attempts=1)
    )
    assert result.status is Status.FAIL
    assert "no_fresh_process_recovery" in result.reasons


@pytest.mark.parametrize("boundary", list(Boundary))
@pytest.mark.parametrize("control", ["noop", "uninstrumented"])
def test_negative_controls_never_pass(recovery, boundary, control):
    result = recovery.run(
        worker_command("uninstrumented" if control == "uninstrumented" else "correct"),
        Scenario(boundary),
        fault_mode="noop" if control == "noop" else "kill",
    )
    assert result.status is Status.INCONCLUSIVE
    assert "missing_fault_witness" in result.reasons
    assert len(result.attempts) == 1
    with pytest.raises(InconclusiveError):
        result.require_pass()


def test_cleanup_kill_cannot_witness_a_noop_actuator(recovery, monkeypatch):
    monkeypatch.setattr(subprocess.Popen, "send_signal", lambda *_args: None)
    result = recovery.run(
        worker_command("correct"), Scenario(Boundary.BEFORE_DISPATCH, timeout=0.4)
    )
    assert result.status is Status.INCONCLUSIVE
    assert result.attempts[0].timed_out
    assert result.attempts[0].returncode == -9  # Cleanup really killed it.
    assert result.fault.sent_signal == 9  # Actuator claimed delivery.
    assert result.fault.wait_returncode is None  # Witness gate remains closed.
    assert not result.fault.witnessed


def test_missing_or_contradictory_wait_evidence_is_inconclusive(recovery):
    result = recovery.run(worker_command("correct"), Scenario(Boundary.AFTER_EFFECT)).require_pass()
    for wait_status in (None, 0, -signal.SIGTERM):
        fault = replace(result.fault, wait_returncode=wait_status)
        status, reasons = _evaluate(
            result.scenario, list(result.attempts), fault, result.final_snapshot, False
        )
        assert status is Status.INCONCLUSIVE
        assert "missing_fault_witness" in reasons


def test_witness_is_on_disk_before_recovery_starts(recovery, tmp_path):
    command = custom_worker(
        tmp_path,
        """import json, os
from pathlib import Path
from crashfixture.workers import work
from crashfixture import Ledger
db = Path(os.environ['CRASHFIXTURE_DB'])
if Ledger(db).snapshot()['tasks'][0]['attempts']:
    assert json.loads((db.parent / 'fault.json').read_text())['wait_returncode'] == -9
raise SystemExit(work('correct'))
""",
    )
    recovery.run(command, Scenario(Boundary.AFTER_EFFECT)).require_pass()


def test_recovery_hang_fails_but_missing_trigger_is_inconclusive(recovery, tmp_path):
    command = custom_worker(
        tmp_path,
        """import os, time
from crashfixture import Ledger
from crashfixture.workers import work
if Ledger(os.environ['CRASHFIXTURE_DB']).snapshot()['tasks'][0]['attempts']:
    time.sleep(60)
raise SystemExit(work('correct'))
""",
    )
    result = recovery.run(command, Scenario(Boundary.BEFORE_DISPATCH, timeout=0.4))
    assert result.status is Status.FAIL
    assert result.fault.witnessed
    assert "recovery_timeout" in result.reasons
    command = custom_worker(tmp_path, "import time; time.sleep(60)")
    result = recovery.run(command, Scenario(Boundary.BEFORE_DISPATCH, timeout=0.2))
    assert result.status is Status.INCONCLUSIVE
    assert result.attempts[0].timed_out


@pytest.mark.parametrize(
    "event",
    ["not-json", "[]", '{"boundary":"before_dispatch","pid":0,"token":"forged"}'],
)
def test_invalid_or_forged_event_is_inconclusive(recovery, tmp_path, event):
    source = (
        "import os, time\n"
        f"os.write(int(os.environ['CRASHFIXTURE_EVENTS_FD']), {(event + chr(10)).encode()!r})\n"
        "time.sleep(60)\n"
    )
    result = recovery.run(custom_worker(tmp_path, source), Scenario(Boundary.BEFORE_DISPATCH))
    assert result.status is Status.INCONCLUSIVE
    assert result.attempts[0].error == "invalid_protocol_event"


@pytest.mark.parametrize(
    "hook_source",
    [
        "probe.hit(Boundary.AFTER_EFFECT)",  # Skipped mandatory preceding hook.
        "probe.hit(Boundary.BEFORE_DISPATCH); probe.hit(Boundary.BEFORE_DISPATCH)",
        "probe.hit(Boundary.BEFORE_DISPATCH); probe.hit(Boundary.AFTER_EFFECT)",  # No commit.
    ],
)
def test_broken_boundary_instrumentation_is_inconclusive(recovery, tmp_path, hook_source):
    source = (
        "import os\nfrom crashfixture import Ledger, Probe, Boundary\n"
        "Ledger(os.environ['CRASHFIXTURE_DB']).begin_attempt()\nprobe = Probe()\n" + hook_source
    )
    result = recovery.run(custom_worker(tmp_path, source), Scenario(Boundary.AFTER_EFFECT))
    assert result.status is Status.INCONCLUSIVE


@pytest.mark.parametrize("payload", [b"x" * 4097, b"unfinished"])
def test_oversized_or_truncated_protocol_is_inconclusive(recovery, tmp_path, payload):
    source = "import os\n" + f"os.write(int(os.environ['CRASHFIXTURE_EVENTS_FD']), {payload!r})"
    result = recovery.run(custom_worker(tmp_path, source), Scenario(Boundary.BEFORE_DISPATCH))
    assert result.status is Status.INCONCLUSIVE
    assert result.attempts[0].error in {"oversized_protocol_event", "truncated_protocol_event"}


def test_unreadable_recovered_ledger_is_inconclusive(recovery, tmp_path):
    source = """import os
from pathlib import Path
from crashfixture import Ledger
from crashfixture.workers import work
db = Path(os.environ['CRASHFIXTURE_DB'])
if Ledger(db).snapshot()['tasks'][0]['attempts']:
    db.write_bytes(b'synthetic-corruption')
else:
    raise SystemExit(work('correct'))
"""
    result = recovery.run(custom_worker(tmp_path, source), Scenario(Boundary.AFTER_EFFECT))
    assert result.status is Status.INCONCLUSIVE
    assert "unreadable_ledger" in result.reasons


def test_spawn_error_is_inconclusive(recovery):
    result = recovery.run(["/nonexistent/synthetic-worker"], Scenario(Boundary.BEFORE_DISPATCH))
    assert result.status is Status.INCONCLUSIVE
    assert result.attempts[0].error == "launch_error"


@pytest.mark.parametrize(
    "change",
    [
        "ALTER TABLE tasks RENAME TO old_tasks; "
        "CREATE TABLE tasks (task_id TEXT, state TEXT, attempts INTEGER); "
        "INSERT INTO tasks VALUES ('synthetic-task', 'succeeded', 2)",
        "ALTER TABLE tasks RENAME TO old_tasks; "
        "CREATE TABLE tasks (task_id TEXT, state TEXT, attempts INTEGER, checkpoint INTEGER); "
        "INSERT INTO tasks VALUES ('synthetic-task', 'succeeded', NULL, 1)",
    ],
)
def test_malformed_readable_recovery_state_is_inconclusive(recovery, tmp_path, change):
    source = (
        "import os\nfrom crashfixture import Ledger\nfrom crashfixture.workers import work\n"
        "ledger = Ledger(os.environ['CRASHFIXTURE_DB'])\n"
        "if not ledger.snapshot()['tasks'][0]['attempts']:\n"
        "    raise SystemExit(work('correct'))\n"
        "with ledger._connection() as conn:\n"
        f"    conn.executescript({change!r})\n"
    )
    result = recovery.run(custom_worker(tmp_path, source), Scenario(Boundary.AFTER_EFFECT))
    assert result.status is Status.INCONCLUSIVE
    assert "invalid_durable_evidence" in result.reasons
    assert json.loads((Path(result.artifact_dir) / "result.json").read_text())["status"] == (
        "inconclusive"
    )


def test_replacing_a_witnessed_effect_cannot_pass_by_final_count(recovery, tmp_path):
    source = """import os
from crashfixture import Ledger
from crashfixture.workers import work
ledger = Ledger(os.environ['CRASHFIXTURE_DB'])
if not ledger.snapshot()['tasks'][0]['attempts']:
    raise SystemExit(work('correct'))
ledger.begin_attempt()
with ledger._connection() as conn:
    conn.execute('DROP TRIGGER effects_no_delete')  # Simulate corrupted destination retention.
    conn.execute('DELETE FROM effects')
ledger.commit_effect('logical-effect')
ledger.checkpoint()
ledger.finish()
"""
    result = recovery.run(custom_worker(tmp_path, source), Scenario(Boundary.AFTER_EFFECT))
    assert result.status is Status.FAIL
    assert "committed_effect_lost" in result.reasons
    assert len(result.final_snapshot["effects"]) == 1
    assert (
        result.final_snapshot["effects"][0]["effect_id"]
        > (result.fault.snapshot["effects"][0]["effect_id"])
    )


def test_durable_attempt_budget_cannot_be_overstated(recovery, tmp_path):
    source = """import os
from crashfixture import Ledger
from crashfixture.workers import work
ledger = Ledger(os.environ['CRASHFIXTURE_DB'])
if not ledger.snapshot()['tasks'][0]['attempts']:
    raise SystemExit(work('correct'))
work('correct')
with ledger._connection() as conn:
    conn.execute('UPDATE tasks SET attempts = 11')
"""
    result = recovery.run(custom_worker(tmp_path, source), Scenario(Boundary.AFTER_EFFECT))
    assert result.status is Status.FAIL
    assert "attempt_budget_exceeded" in result.reasons


def test_ambient_environment_is_not_copied_and_noisy_stdout_does_not_block(
    recovery, tmp_path, monkeypatch
):
    monkeypatch.setenv("SYNTHETIC_AMBIENT_CANARY", "synthetic")
    source = """import os
from crashfixture.workers import work
assert 'SYNTHETIC_AMBIENT_CANARY' not in os.environ
os.write(1, b'synthetic-output' * 100000)
raise SystemExit(work('correct'))
"""
    recovery.run(custom_worker(tmp_path, source), Scenario(Boundary.BEFORE_DISPATCH)).require_pass()


def test_only_owned_process_group_is_cleaned(recovery, tmp_path):
    source = """import os, subprocess, sys
from pathlib import Path
from crashfixture.workers import work
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
with Path(os.environ['CRASHFIXTURE_DB']).with_suffix('.child').open('a') as stream:
    stream.write(str(child.pid) + '\\n')
raise SystemExit(work('correct'))
"""
    result = recovery.run(custom_worker(tmp_path, source), Scenario(Boundary.BEFORE_DISPATCH))
    result.require_pass()
    pids = (Path(result.artifact_dir) / "ledger.child").read_text().splitlines()
    assert len(pids) == 2
    for owned_pid in pids:
        pid = int(owned_pid)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            stat = Path(f"/proc/{pid}/stat")
            if stat.exists() and stat.read_text().split(") ", 1)[1].startswith("Z"):
                break  # Linux container init may retain a stopped zombie.
            time.sleep(0.01)
        else:
            pytest.fail(f"owned descendant {pid} survived cleanup")
        assert os.getpid() != pid


@pytest.mark.parametrize(
    "kwargs",
    [
        {"boundary": "before_dispatch"},
        {"max_attempts": 0},
        {"max_attempts": 11},
        {"max_attempts": True},
        {"timeout": 0},
        {"timeout": float("nan")},
        {"timeout": float("inf")},
        {"timeout": 31},
        {"expected_effects": 2},
        {"expected_effects": True},
        {"expected_terminal": "running"},
    ],
)
def test_scenario_has_finite_validated_limits(kwargs):
    with pytest.raises(ValueError):
        Scenario(**{"boundary": Boundary.BEFORE_DISPATCH, **kwargs})


def test_argv_and_reserved_environment_are_validated(recovery):
    for command in ("echo synthetic", [], [""], [1]):
        with pytest.raises(ValueError):
            recovery.run(command, Scenario(Boundary.BEFORE_DISPATCH))
    with pytest.raises(ValueError, match="reserved"):
        recovery.run(
            worker_command("correct"),
            Scenario(Boundary.BEFORE_DISPATCH),
            env={"CRASHFIXTURE_DB": "override"},
        )
