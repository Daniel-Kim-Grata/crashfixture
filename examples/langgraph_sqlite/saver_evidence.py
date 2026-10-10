"""Example-specific saver evidence; the core fixture validates the ledger separately."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

from crashfixture import Boundary, Result, Status

THREAD_ID = "synthetic-saver-thread"
EFFECT_KEY = "synthetic-saver-effect"
CONFIG = {"configurable": {"thread_id": THREAD_ID, "checkpoint_ns": ""}}


def validate_read(read: Any) -> None:
    """Reject malformed evidence rather than accepting Python's bool/int equality."""
    if read is None:
        return
    if (
        not isinstance(read, dict)
        or set(read) != {"config", "checkpoint_id", "channel_values", "writer_token"}
        or not isinstance(read["config"], dict)
        or set(read["config"]) != {"thread_id", "checkpoint_ns", "checkpoint_id"}
        or not all(isinstance(value, str) for value in read["config"].values())
        or not isinstance(read["checkpoint_id"], str)
        or not read["checkpoint_id"]
        or read["config"]["checkpoint_id"] != read["checkpoint_id"]
        or not isinstance(read["channel_values"], dict)
        or set(read["channel_values"]) != {"effect_key", "acknowledged"}
        or not isinstance(read["channel_values"]["effect_key"], str)
        or type(read["channel_values"]["acknowledged"]) is not bool
        or not isinstance(read["writer_token"], str)
        or not read["writer_token"]
    ):
        raise ValueError("invalid saver read evidence")


def read_checkpoint(path: Path, config: dict[str, Any]) -> dict[str, Any] | None:
    """Read through the real public saver on a separate SQLite connection."""
    with SqliteSaver.from_conn_string(str(path)) as saver:
        saved = saver.get_tuple(config)
    if saved is None:
        return None
    return {
        "config": saved.config["configurable"],
        "checkpoint_id": saved.checkpoint["id"],
        "channel_values": saved.checkpoint["channel_values"],
        "writer_token": saved.metadata["writer_token"],
    }


def inspect_saver(result: Result) -> dict[str, Any]:
    """Combine the SIGKILL/ledger gate with this example's durable saver gate.

    Missing receipts are inconclusive. A witnessed, well-observed wrong resume
    remains a failure even if destination deduplication made the core gate pass.
    This is a trusted-worker illustration, not graph-runtime or saver conformance.
    """
    verdict = {"status": result.status.value, "reasons": list(result.reasons)}
    if result.status is Status.INCONCLUSIVE:
        return verdict
    root = Path(result.artifact_dir)

    def receipt(attempt, stage):
        value = json.loads((root / f"saver-{attempt.token}-{stage}.json").read_text())
        if set(value) != {"schema_version", "pid", "token", "attempt", "thread_id", "read"}:
            raise ValueError("unexpected receipt schema")
        if (
            type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or type(value["pid"]) is not int
            or value["pid"] != attempt.pid
            or value["token"] != attempt.token
            or type(value["attempt"]) is not int
            or value["attempt"] != attempt.number
            or not isinstance(value["thread_id"], str)
        ):
            raise ValueError("receipt does not identify the observed child")
        validate_read(value["read"])
        return value

    try:
        first, restarted = result.attempts
        initial = receipt(first, "start")
        resumed = receipt(restarted, "start")
        writer = first if result.scenario.boundary is Boundary.AFTER_CHECKPOINT else restarted
        committed = receipt(writer, "committed")["read"]
        database = root / "saver.sqlite3"
        if not database.is_file():
            raise ValueError("missing saver database")
        final = read_checkpoint(database, CONFIG)
        validate_read(final)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, sqlite3.Error):
        return {"status": "inconclusive", "reasons": ["missing_or_invalid_saver_evidence"]}

    reasons = list(result.reasons)
    if initial["thread_id"] != THREAD_ID or initial["read"] is not None:
        reasons.append("unexpected_initial_saver_state")
    if resumed["thread_id"] != THREAD_ID:
        reasons.append("resume_thread_changed")
    expected_read = committed if result.scenario.boundary is Boundary.AFTER_CHECKPOINT else None
    if resumed["read"] != expected_read:
        reasons.append("recovered_checkpoint_mismatch")
    if (
        committed is None
        or committed["writer_token"] != writer.token
        or committed["channel_values"] != {"effect_key": EFFECT_KEY, "acknowledged": True}
        or committed["config"]
        != {
            "thread_id": THREAD_ID,
            "checkpoint_ns": "",
            "checkpoint_id": committed["checkpoint_id"],
        }
    ):
        reasons.append("unexpected_committed_saver_state")
    if final != committed:
        reasons.append("final_saver_checkpoint_mismatch")
    return {"status": "fail" if reasons else "pass", "reasons": list(dict.fromkeys(reasons))}
