"""Exactly two local fresh-process resumes; explicit barriers and lock witness."""

from __future__ import annotations

import fcntl
import json
import os
import select
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

from graph_evidence import (
    expected_status,
    physical_effects,
    receipt,
    terminal_reasons,
    validate_read,
    visible_status,
)
from graph_worker import read_graph, require_identity
from worker import BUSINESS_KEY

from crashfixture import Boundary, Harness, Ledger, Scenario

WORKER = Path(__file__).with_name("graph_worker.py").resolve()


def cleanup(children):
    errors = []
    for child in children:
        try:
            os.killpg(child["process"].pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as error:
            errors.append(f"kill group {child['process'].pid}: {error}")
        try:
            child["process"].wait(timeout=1)
        except (OSError, subprocess.SubprocessError) as error:
            errors.append(f"reap child {child['process'].pid}: {error}")
        finally:
            for descriptor in (child["read"], child["write"]):
                try:
                    os.close(descriptor)
                except OSError as error:
                    errors.append(f"close owned descriptor {descriptor}: {error}")
    if errors:
        raise RuntimeError("concurrent cleanup incomplete: " + "; ".join(errors))


def round_events(children, allowed, deadline):
    wanted = [len(child["events"]) + 1 for child in children]
    while any(len(child["events"]) < count for child, count in zip(children, wanted, strict=True)):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("missing concurrent-resume barrier evidence")
        reads = [
            child["read"]
            for child, count in zip(children, wanted, strict=True)
            if len(child["events"]) < count
        ]
        available, _, _ = select.select(reads, [], [], remaining)
        for descriptor in available:
            child = next(child for child in children if child["read"] == descriptor)
            data = os.read(descriptor, 4096)
            if not data:
                raise ValueError("child exited before expected barrier")
            child["buffer"] += data
            if len(child["buffer"]) > 4096:
                raise ValueError("oversized child barrier")
            if b"\n" in child["buffer"]:
                line, child["buffer"] = child["buffer"].split(b"\n", 1)
                event = json.loads(line)
                if (
                    set(event) != {"stage", "pid", "token"}
                    or type(event["pid"]) is not int
                    or event["pid"] != child["process"].pid
                    or event["token"] != child["token"]
                    or event["stage"] not in allowed
                    or child["buffer"]
                ):
                    raise ValueError("invalid exact-child barrier")
                child["events"].append(event)
    return [child["events"][-1]["stage"] for child in children]


def release(children):
    for child in children:
        if os.write(child["write"], b"+") != 1:
            raise RuntimeError("short barrier release")


def resumes(root, mode, guarded):
    children = []
    deadline = time.monotonic() + 12
    lock_witness = False
    try:
        for _ in range(2):
            ready_read, ready_write = os.pipe()
            go_read, go_write = os.pipe()
            token = uuid.uuid4().hex
            command = [
                sys.executable,
                str(WORKER),
                mode,
                "--resume-root",
                str(root),
                "--token",
                token,
                "--ready-fd",
                str(ready_write),
                "--go-fd",
                str(go_read),
            ]
            if guarded:
                command.append("--guard")
            try:
                process = subprocess.Popen(
                    command,
                    cwd=root,
                    env={
                        "PATH": os.defpath,
                        "HOME": str(root / "home"),
                        "TMPDIR": str(root / "tmp"),
                    },
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    pass_fds=(ready_write, go_read),
                    start_new_session=True,
                )
            except Exception:
                os.close(ready_read)
                os.close(go_write)
                raise
            finally:
                os.close(ready_write)
                os.close(go_read)
            children.append(
                {
                    "process": process,
                    "token": token,
                    "read": ready_read,
                    "write": go_write,
                    "buffer": b"",
                    "events": [],
                }
            )
        round_events(children, {"admitted"}, deadline)
        release(children)
        stages = round_events(children, {"owner", "blocked"} if guarded else {"effect"}, deadline)
        if guarded:
            if sorted(stages) != ["blocked", "owner"] or not (root / "resume.lock").is_file():
                raise ValueError("missing exact owner/contender evidence")
            with (root / "resume.lock").open("r+") as witness:
                try:
                    fcntl.flock(witness, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    lock_witness = True
                else:
                    raise ValueError("parent could acquire claimed owned lock")
        release(children)
        launches = []
        for child in children:
            code = child["process"].wait(timeout=max(0.01, deadline - time.monotonic()))
            if code != 0:
                raise RuntimeError("concurrent graph child failed")
            launches.append(
                {
                    "pid": child["process"].pid,
                    "token": child["token"],
                    "returncode": code,
                    "events": child["events"],
                }
            )
        return launches, lock_witness
    finally:
        cleanup(children)  # Parent-owned groups only, including error/timeout paths.


def run(root, mode, guarded):
    boundary = Boundary.BEFORE_DISPATCH if not guarded else Boundary.AFTER_EFFECT
    # Stop after the killed launch. Seed core FAIL is deliberate: recovery is
    # evaluated below using the two separately observed resumes, never inherited.
    seed = Harness(root).run(
        [sys.executable, str(WORKER), mode], Scenario(boundary, max_attempts=1, timeout=6)
    )
    directory = Path(seed.artifact_dir)
    report = {
        "mode": mode,
        "guarded": guarded,
        "seed_core": seed.to_dict(),
        "status": "inconclusive",
        "reasons": ["missing_concurrent_evidence"],
    }
    try:
        if not seed.fault or not seed.fault.witnessed or len(seed.attempts) != 1:
            raise ValueError("missing seed SIGKILL witness")
        persisted = read_graph(directory)
        validate_read(persisted)
        require_identity(persisted)
        first = seed.attempts[0]
        if persisted != receipt(directory, first.token, first.pid, 1, "intent") or persisted[
            "next"
        ] != ["effect"]:
            raise ValueError("invalid seeded pending graph")
        launches, lock_witness = resumes(directory, mode, guarded)
        final = read_graph(directory)
        validate_read(final)
        effects = physical_effects(directory)
        ledger = Ledger(directory / "ledger.sqlite3").snapshot()
        hold = mode == "no_lookup"
        phase = "reconciliation_hold" if hold else "acknowledged"
        reasons = terminal_reasons(final, effects, ledger, phase, 1)
        if len(effects) > 1:
            reasons.append("duplicate_synthetic_effects")
        attempts = []
        observed_tokens = {launch["token"] for launch in launches}
        starts, completions = {}, {}
        for launch in launches:
            data = json.loads((directory / f"graph-{launch['token']}-done.json").read_text())
            attempt = data["attempt"]
            if type(attempt) is not int:
                raise ValueError("malformed child attempt")
            completed = receipt(directory, launch["token"], launch["pid"], attempt, "done")
            # Check child identity independently of the global provider-count gate.
            if terminal_reasons(completed, [], {"effects": []}, phase, 0):
                reasons.append("invalid_child_terminal_graph")
            if completed is None or completed["writer_token"] not in observed_tokens:
                reasons.append("unobserved_child_graph_writer")
            start = receipt(directory, launch["token"], launch["pid"], attempt, "start")
            require_identity(start)
            stage = launch["events"][-1]["stage"]
            starts[stage] = start
            completions[stage] = completed
            if not guarded and start != persisted:
                reasons.append("concurrent_resume_checkpoint_mismatch")
            if (
                guarded
                and stage == "owner"
                and (
                    start != persisted
                    or attempt != 2
                    or completed["writer_token"] != launch["token"]
                )
            ):
                reasons.append("guarded_owner_checkpoint_mismatch")
            if guarded and stage == "blocked" and attempt != 3:
                reasons.append("guarded_contender_attempt_mismatch")
            attempts.append(attempt)
        if guarded and (
            starts["blocked"] != completions["owner"]
            or completions["blocked"] != completions["owner"]
            or final != completions["owner"]
        ):
            reasons.append("guarded_contender_checkpoint_mismatch")
        if sorted(attempts) != [2, 3] or ledger["tasks"] != [
            {
                "task_id": "synthetic-task",
                "state": "failed" if hold else "succeeded",
                "attempts": 3,
                "checkpoint": 0 if hold else 1,
            }
        ]:
            reasons.append("concurrent_task_accounting")
        if final is None or final["writer_token"] not in observed_tokens:
            reasons.append("unobserved_final_graph_writer")
        if guarded and not lock_witness:
            raise ValueError("missing physical lock witness")
        if mode == "stable_key" and any(
            effect["operation_key"] != BUSINESS_KEY for effect in effects
        ):
            reasons.append("unstable_business_key")
        visible = visible_status(directory)
        if visible != expected_status(hold):
            reasons.append("concurrent_visible_status")
        report.update(
            status="fail" if reasons else "pass",
            reasons=list(dict.fromkeys(reasons)),
            launches=launches,
            lock_contention_witness=lock_witness,
            final_graph=final,
            provider_effects=effects,
            final_ledger=ledger,
            visible_status=visible,
        )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        RuntimeError,
        sqlite3.Error,
        subprocess.SubprocessError,
    ) as error:
        report.update(
            status="inconclusive",
            reasons=["missing_or_invalid_concurrent_evidence"],
            detail=str(error),
        )
    (directory / "concurrent-result.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
