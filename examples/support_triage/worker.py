"""One deterministic synthetic support ticket; local saver and mock provider only."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import threading
import uuid
from pathlib import Path

from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.sqlite import SqliteSaver

from crashfixture import Boundary, Ledger, Probe

TICKET = {"id": "fixture-ticket-001", "text": "refund status delayed"}
ROUTE = "billing" if "refund" in TICKET["text"] else "general"
BUSINESS_KEY = f"{TICKET['id']}:create-followup:v1"
CONFIG = {"configurable": {"thread_id": TICKET["id"], "checkpoint_ns": ""}}


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def read_saver(root):
    with SqliteSaver.from_conn_string(str(root / "saver.sqlite3")) as saver:
        saved = saver.get_tuple(CONFIG)
    if saved is None:
        return None
    return {
        "checkpoint_id": saved.checkpoint["id"],
        "config": saved.config["configurable"],
        "channels": saved.checkpoint["channel_values"],
        "writer_token": saved.metadata["writer_token"],
    }


def checkpoint(root, phase, token, attempt):
    value = empty_checkpoint()
    value["channel_values"] = {
        "ticket_id": TICKET["id"],
        "route": ROUTE,
        "business_key": BUSINESS_KEY,
        "phase": phase,
    }
    value["channel_versions"] = {key: "1" for key in value["channel_values"]}
    with SqliteSaver.from_conn_string(str(root / "saver.sqlite3")) as saver:
        saver.put(
            CONFIG,
            value,
            {"source": "loop", "step": attempt, "parents": {}, "writer_token": token},
            value["channel_versions"],
        )
    return read_saver(root)  # Separate connection observes the committed state.


def provider_setup(root):
    with sqlite3.connect(root / "provider.sqlite3") as connection:
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS effects (effect_id INTEGER PRIMARY KEY, "
            "operation_key TEXT UNIQUE NOT NULL, business_key TEXT NOT NULL, "
            "ticket_id TEXT NOT NULL, route TEXT NOT NULL)"
        )


def dispatch(root, mode, token):
    # Only stable_key/stalled represent a provider supporting request deduplication.
    operation_key = BUSINESS_KEY if mode in {"stable_key", "stalled"} else token
    with sqlite3.connect(root / "provider.sqlite3") as connection:
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            "INSERT OR IGNORE INTO effects(operation_key,business_key,ticket_id,route) "
            "VALUES (?,?,?,?)",
            (operation_key, BUSINESS_KEY, TICKET["id"], ROUTE),
        )
    return operation_key


def provider_snapshot(root):
    with sqlite3.connect(root / "provider.sqlite3") as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute("SELECT * FROM effects ORDER BY effect_id")]


def visible_status(root, phase):
    complete = phase == "acknowledged"
    write_json(
        root / "workflow-status.json",
        {
            "ticket_id": TICKET["id"],
            "phase": phase,
            "business_complete": complete,
            "automatic_retry_allowed": complete,
            "action_required": None
            if complete
            else "reconcile provider outcome before retry"
            if phase == "reconciliation_hold"
            else "investigate stalled recovery",
        },
    )


def receipt(root, token, attempt, stage, read):
    write_json(
        root / f"triage-{token}-{stage}.json",
        {"schema_version": 1, "pid": os.getpid(), "token": token, "attempt": attempt, "read": read},
    )


def require_identity(loaded):
    """Only this original synthetic ticket's valid checkpoint may resume."""
    if loaded is None:
        return
    channels = loaded.get("channels")
    checkpoint_id = loaded.get("checkpoint_id")
    if (
        not isinstance(checkpoint_id, str)
        or not checkpoint_id
        or not isinstance(loaded.get("writer_token"), str)
        or not loaded["writer_token"]
        or loaded.get("config") != {**CONFIG["configurable"], "checkpoint_id": checkpoint_id}
        or not isinstance(channels, dict)
        or set(channels) != {"ticket_id", "route", "business_key", "phase"}
        or channels["ticket_id"] != TICKET["id"]
        or channels["route"] != ROUTE
        or channels["business_key"] != BUSINESS_KEY
        or channels["phase"] not in {"intent", "acknowledged", "reconciliation_hold"}
    ):
        raise ValueError("checkpoint identity does not match this synthetic business operation")


def resume_terminal(root, mode, loaded, destination):
    """The same terminal guard handles harness restart and duplicate delivery."""
    require_identity(loaded)
    phase = loaded["channels"]["phase"] if loaded else None
    if phase == "acknowledged":
        if mode == "stalled":
            visible_status(root, "stalled")
            threading.Event().wait()  # Harness owns this child and bounds its timeout.
        visible_status(root, phase)
        destination.finish()
        return True
    if phase == "reconciliation_hold":
        visible_status(root, phase)
        destination.finish("failed")
        return True
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("unsafe", "stable_key", "no_lookup", "stalled"))
    parser.add_argument("--replay", type=Path)
    args = parser.parse_args()
    if args.replay:
        # Bounded sequential duplicate delivery after terminal/hold state only.
        root = args.replay.resolve()
        loaded = read_saver(root)
        if loaded is None or not resume_terminal(
            root, args.mode, loaded, Ledger(root / "ledger.sqlite3")
        ):
            raise ValueError("replay requires a terminal or reconciliation-hold checkpoint")
        receipt(root, uuid.uuid4().hex, 0, "replay", loaded)
        return

    destination = Ledger(os.environ["CRASHFIXTURE_DB"])
    root = destination.path.parent
    probe = Probe()
    attempt = destination.begin_attempt()
    provider_setup(root)
    loaded = read_saver(root)
    receipt(root, probe.token, attempt, "start", loaded)
    phase = loaded["channels"]["phase"] if loaded else None
    if resume_terminal(root, args.mode, loaded, destination):
        return
    if phase == "intent" and args.mode == "no_lookup":
        observed = checkpoint(root, "reconciliation_hold", probe.token, attempt)
        receipt(root, probe.token, attempt, "committed", observed)
        visible_status(root, "reconciliation_hold")
        destination.finish("failed")
        return

    # Durable intent precedes dispatch. Without lookup/dedup, even a pre-effect
    # crash leaves the worker uncertain: it must hold rather than guess/retry.
    intended = checkpoint(root, "intent", probe.token, attempt)
    receipt(root, probe.token, attempt, "intent", intended)
    probe.hit(Boundary.BEFORE_DISPATCH)
    operation_key = dispatch(root, args.mode, probe.token)
    destination.commit_effect(operation_key)  # Cooperative provider mirror for core gate.
    probe.hit(Boundary.AFTER_EFFECT)
    observed = checkpoint(root, "acknowledged", probe.token, attempt)
    receipt(root, probe.token, attempt, "committed", observed)
    destination.checkpoint()
    probe.hit(Boundary.AFTER_CHECKPOINT)
    visible_status(root, "acknowledged")
    destination.finish()


if __name__ == "__main__":
    main()
