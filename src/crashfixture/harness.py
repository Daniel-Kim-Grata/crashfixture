"""Bounded POSIX child control and independent evidence evaluation."""

from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .ledger import TASK_ID, Ledger
from .model import Attempt, Boundary, Fault, Result, Scenario, Status

BOUNDARIES = list(Boundary)


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _snapshot_valid(snapshot: dict[str, Any]) -> bool:
    if set(snapshot) != {"tasks", "effects"}:
        return False
    tasks, effects = snapshot["tasks"], snapshot["effects"]
    if not isinstance(tasks, list) or not isinstance(effects, list):
        return False
    for task in tasks:
        if not isinstance(task, dict) or set(task) != {
            "task_id",
            "state",
            "attempts",
            "checkpoint",
        }:
            return False
        if (
            not isinstance(task["task_id"], str)
            or task["state"] not in {"pending", "running", "succeeded", "failed"}
            or type(task["attempts"]) is not int
            or task["attempts"] < 0
            or type(task["checkpoint"]) is not int
            or task["checkpoint"] not in {0, 1}
        ):
            return False
    for effect in effects:
        if not isinstance(effect, dict) or set(effect) != {
            "effect_id",
            "task_id",
            "effect_key",
            "payload",
        }:
            return False
        if (
            type(effect["effect_id"]) is not int
            or effect["effect_id"] <= 0
            or not all(isinstance(effect[k], str) for k in ("task_id", "effect_key", "payload"))
            or not effect["effect_key"]
        ):
            return False
    return len({e["effect_id"] for e in effects}) == len(effects)


def _boundary_valid(boundary: Boundary, snapshot: dict[str, Any]) -> bool:
    if not _snapshot_valid(snapshot):
        return False
    tasks, effects = snapshot["tasks"], snapshot["effects"]
    if len(tasks) != 1:
        return False
    task = tasks[0]
    return (
        task["task_id"] == TASK_ID
        and task["state"] == "running"
        and task["attempts"] == 1
        and task["checkpoint"] == int(boundary is Boundary.AFTER_CHECKPOINT)
        and len(effects) == int(boundary is not Boundary.BEFORE_DISPATCH)
        and all(e["task_id"] == TASK_ID and e["payload"] == "synthetic-effect" for e in effects)
    )


def _evaluate(
    scenario: Scenario,
    attempts: list[Attempt],
    fault: Fault | None,
    snapshot: dict[str, Any],
    ledger_error: bool,
) -> tuple[Status, tuple[str, ...]]:
    invalid = []
    if fault is None or not fault.witnessed:
        invalid.append("missing_fault_witness")
    if fault is not None and not fault.snapshot_valid:
        invalid.append("invalid_boundary_snapshot")
    if any(a.error for a in attempts):
        invalid.append("harness_or_protocol_error")
    if ledger_error:
        invalid.append("unreadable_ledger")
    if not _snapshot_valid(snapshot):
        invalid.append("invalid_durable_evidence")
    if invalid:
        return Status.INCONCLUSIVE, tuple(invalid)

    failures = []
    if len(attempts) < 2 or attempts[1].token == attempts[0].token:
        failures.append("no_fresh_process_recovery")
    if attempts[-1].timed_out:
        failures.append("recovery_timeout")
    if attempts[-1].returncode != 0:
        failures.append("nonzero_terminal_exit")
    if len(attempts) > scenario.max_attempts:
        failures.append("attempt_budget_exceeded")
    tasks, effects = snapshot["tasks"], snapshot["effects"]
    if any(effect not in effects for effect in fault.snapshot["effects"]):
        failures.append("committed_effect_lost")
    if len(tasks) != 1 or tasks[0]["task_id"] != TASK_ID:
        failures.append("task_identity")
    else:
        task = tasks[0]
        if task["attempts"] != len(attempts):
            failures.append("attempt_accounting")
        if task["attempts"] > scenario.max_attempts:
            failures.append("attempt_budget_exceeded")
        if task["state"] != scenario.expected_terminal:
            failures.append("terminal_outcome")
        if task["checkpoint"] != int(scenario.expected_terminal == "succeeded"):
            failures.append("checkpoint_state")
    if len(effects) > scenario.expected_effects:
        failures.append("duplicate_synthetic_effects")
    elif len(effects) < scenario.expected_effects:
        failures.append("missing_synthetic_effect")
    if any(e["task_id"] != TASK_ID or e["payload"] != "synthetic-effect" for e in effects):
        failures.append("effect_identity")
    return (Status.FAIL, tuple(dict.fromkeys(failures))) if failures else (Status.PASS, ())


class Harness:
    """Run trusted, instrumented synthetic workers. This is not a sandbox.

    Every invocation gets its own ledger and process group. Only groups created
    by this harness are killed. stdout/stderr are discarded to bound resources;
    checkpoint traffic travels through separate inherited file descriptors.
    """

    def __init__(self, root: str | Path):
        if os.name != "posix":
            raise OSError("crashfixture requires POSIX signals and pass_fds")
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        command: Sequence[str],
        scenario: Scenario,
        *,
        env: Mapping[str, str] | None = None,
        fault_mode: str = "kill",
    ) -> Result:
        if (
            isinstance(command, (str, bytes))
            or not command
            or not all(isinstance(arg, str) and arg for arg in command)
        ):
            raise ValueError(
                "command must be a nonempty argv sequence; shell syntax is not accepted"
            )
        if fault_mode not in {"kill", "noop"}:
            raise ValueError("fault_mode must be kill or noop (negative control)")
        if env and any(key.startswith("CRASHFIXTURE_") for key in env):
            raise ValueError("CRASHFIXTURE_ environment variables are reserved")

        directory = self.root / uuid.uuid4().hex
        directory.mkdir()
        (directory / "home").mkdir()
        (directory / "tmp").mkdir()
        ledger = Ledger(directory / "ledger.sqlite3")
        ledger.initialize()
        _write_json(directory / "scenario.json", asdict(scenario))
        # Avoid handing ambient credentials/configuration to synthetic workers.
        child_env = {
            "PATH": os.defpath,
            "HOME": str(directory / "home"),
            "TMPDIR": str(directory / "tmp"),
            "LC_ALL": "C",
            "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
            **(env or {}),
            "CRASHFIXTURE_DB": str(ledger.path),
            "CRASHFIXTURE_MAX_ATTEMPTS": str(scenario.max_attempts),
        }
        attempts: list[Attempt] = []
        fault = None
        for number in range(1, scenario.max_attempts + 1):
            attempt, observed_fault = self._attempt(
                command, scenario, child_env, number, ledger, directory, fault_mode
            )
            attempts.append(attempt)
            _write_json(directory / f"attempt-{number}.json", asdict(attempt))
            if observed_fault is not None:
                fault = observed_fault
                # Parent-owned wait-status witness is saved BEFORE any restart.
                _write_json(directory / "fault.json", asdict(fault))
            if attempt.error or attempt.timed_out or attempt.returncode == 0:
                break
            if number == 1 and (fault is None or not fault.witnessed):
                break
        ledger_error = False
        try:
            snapshot = ledger.snapshot()
        except Exception:
            snapshot = {"tasks": [], "effects": []}
            ledger_error = True
        status, reasons = _evaluate(scenario, attempts, fault, snapshot, ledger_error)
        result = Result(scenario, status, reasons, tuple(attempts), fault, snapshot, str(directory))
        _write_json(directory / "result.json", result.to_dict())
        return result

    def _attempt(
        self,
        command: Sequence[str],
        scenario: Scenario,
        env: dict[str, str],
        number: int,
        ledger: Ledger,
        directory: Path,
        fault_mode: str,
    ) -> tuple[Attempt, Fault | None]:
        events_read, events_write = os.pipe()
        acks_read, acks_write = os.pipe()
        token = uuid.uuid4().hex
        proc = None
        fault = None
        events: list[str] = []
        error = None
        timed_out = False
        witness_returncode = None
        try:
            try:
                proc = subprocess.Popen(
                    list(command),
                    cwd=directory,
                    env={
                        **env,
                        "CRASHFIXTURE_EVENTS_FD": str(events_write),
                        "CRASHFIXTURE_ACKS_FD": str(acks_read),
                        "CRASHFIXTURE_TOKEN": token,
                    },
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    pass_fds=(events_write, acks_read),
                    start_new_session=True,
                )
            except (OSError, ValueError):
                error = "launch_error"
                return Attempt(number, token, None, None, (), error=error), None
            os.close(events_write)
            events_write = -1
            os.close(acks_read)
            acks_read = -1
            deadline = time.monotonic() + scenario.timeout
            buffer = b""
            eof = False
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                readable, _, _ = select.select(
                    [] if eof else [events_read], [], [], min(0.01, remaining)
                )
                if readable:
                    data = os.read(events_read, 4096)
                    if not data:
                        eof = True
                    buffer += data
                    if len(buffer) > 4096:
                        error = "oversized_protocol_event"
                        break
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        try:
                            event = json.loads(line)
                            boundary = Boundary(event["boundary"])
                            if event["pid"] != proc.pid or event["token"] != token:
                                raise ValueError("process identity mismatch")
                            if events and BOUNDARIES.index(boundary) <= BOUNDARIES.index(
                                Boundary(events[-1])
                            ):
                                raise ValueError("out of order or duplicate boundary")
                            if number == 1 and BOUNDARIES.index(boundary) != len(events):
                                raise ValueError("initial boundary sequence is incomplete")
                        except (ValueError, KeyError, TypeError):
                            error = "invalid_protocol_event"
                            break
                        events.append(boundary.value)
                        if number == 1 and boundary is scenario.boundary:
                            snapshot = ledger.snapshot()
                            valid = _boundary_valid(boundary, snapshot)
                            sent = None
                            if fault_mode == "kill" and valid:
                                proc.send_signal(signal.SIGKILL)
                                sent = int(signal.SIGKILL)
                            fault = Fault(
                                boundary.value, token, proc.pid, sent, None, snapshot, valid
                            )
                            if sent is not None or not valid:
                                break
                        os.write(acks_write, b"+")
                    if error or (
                        fault is not None and (fault.sent_signal or not fault.snapshot_valid)
                    ):
                        break
                if proc.poll() is not None:
                    if buffer:
                        error = "truncated_protocol_event"
                    break
            if fault is not None and fault.sent_signal:
                # Do not let the cleanup kill below manufacture a witness when
                # the actuator silently failed and this wait timed out.
                witness_returncode = proc.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out = True
        except Exception:
            error = "observer_error"
        finally:
            if proc is not None:
                # Include forked descendants; never scan or kill other owners' processes.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=1.0)
            for fd in (events_read, events_write, acks_read, acks_write):
                if fd >= 0:
                    os.close(fd)
        if fault is not None:
            fault = replace(fault, wait_returncode=witness_returncode)
        return (
            Attempt(number, token, proc.pid, proc.returncode, tuple(events), timed_out, error),
            fault,
        )
