"""Actual local StateGraph execution; one original deterministic ticket."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from worker import (
    BUSINESS_KEY,
    CONFIG,
    ROUTE,
    TICKET,
    dispatch,
    provider_setup,
    visible_status,
    write_json,
)

from crashfixture import Boundary, Ledger, Probe


class TicketState(TypedDict):
    ticket_id: str
    route: str
    business_key: str
    phase: str


def snapshot(graph):
    saved = graph.get_state(CONFIG)
    if not saved.metadata:
        return None
    return {
        "values": saved.values,
        "next": list(saved.next),
        "config": saved.config["configurable"],
        "writer_token": saved.metadata.get("writer_token"),
        "task_errors": [task.error for task in saved.tasks if task.error],
    }


def require_identity(read):
    if read is None:
        return
    values = read["values"]
    if (
        values.get("ticket_id") != TICKET["id"]
        or values.get("route") != ROUTE
        or values.get("business_key") != BUSINESS_KEY
        or values.get("phase") not in {"intent", "acknowledged", "reconciliation_hold"}
        or read["config"].get("thread_id") != TICKET["id"]
        or read["config"].get("checkpoint_ns") != ""
        or not isinstance(read["config"].get("checkpoint_id"), str)
        or not isinstance(read["writer_token"], str)
        or read["task_errors"]
    ):
        raise ValueError("graph checkpoint identity does not match this synthetic operation")


def graph_for(saver, root, mode, token, attempt, probe=None, barrier=None):
    def record(stage):
        observed = snapshot(graph)
        write_json(
            root / f"graph-{token}-{stage}.json",
            {
                "schema_version": 1,
                "pid": os.getpid(),
                "token": token,
                "attempt": attempt,
                "read": observed,
            },
        )

    def route(state):
        if state["ticket_id"] != TICKET["id"]:
            raise ValueError("unknown synthetic ticket")
        return {"route": ROUTE, "business_key": BUSINESS_KEY, "phase": "routed"}

    def intent(state):
        return {"phase": "intent"}

    def effect(state):
        if attempt > 1 and mode == "no_lookup":
            return {"phase": "reconciliation_hold"}
        record("intent")
        if probe:
            probe.hit(Boundary.BEFORE_DISPATCH)
        if barrier:
            barrier("effect")
        operation_key = dispatch(root, mode, token)
        Ledger(root / "ledger.sqlite3").commit_effect(operation_key)
        if probe:
            probe.hit(Boundary.AFTER_EFFECT)
        return {"phase": "acknowledged"}

    def finalize(state):
        # sync durability commits the preceding effect-node update before entry.
        record("committed")
        Ledger(root / "ledger.sqlite3").checkpoint()
        if probe:
            probe.hit(Boundary.AFTER_CHECKPOINT)
        return {}

    builder = StateGraph(TicketState)
    builder.add_node("route", route)
    builder.add_node("intent", intent)
    builder.add_node("effect", effect)
    builder.add_node("finalize", finalize)
    builder.add_edge(START, "route")
    builder.add_edge("route", "intent")
    builder.add_edge("intent", "effect")
    builder.add_conditional_edges(
        "effect",
        lambda state: state["phase"],
        {
            "acknowledged": "finalize",
            "reconciliation_hold": END,
        },
    )
    builder.add_edge("finalize", END)
    graph = builder.compile(checkpointer=saver)
    return graph


def read_graph(root):
    if not (root / "graph.sqlite3").is_file():
        raise ValueError("missing graph database")
    with SqliteSaver.from_conn_string(str(root / "graph.sqlite3")) as saver:
        graph = graph_for(saver, root, "stable_key", "parent-read-only", 0)
        return snapshot(graph)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("unsafe", "stable_key", "no_lookup"))
    parser.add_argument("--resume-root", type=Path)
    parser.add_argument("--token")
    parser.add_argument("--ready-fd", type=int)
    parser.add_argument("--go-fd", type=int)
    parser.add_argument("--guard", action="store_true")
    args = parser.parse_args()
    destination = Ledger(
        (args.resume_root / "ledger.sqlite3") if args.resume_root else os.environ["CRASHFIXTURE_DB"]
    )
    root = destination.path.parent
    probe = None if args.resume_root else Probe()
    token = args.token if args.resume_root else probe.token

    def barrier(stage):
        event = json.dumps({"stage": stage, "pid": os.getpid(), "token": token}).encode() + b"\n"
        if os.write(args.ready_fd, event) != len(event) or os.read(args.go_fd, 1) != b"+":
            raise RuntimeError("concurrent-resume barrier failed")

    lock = None
    try:
        if args.resume_root:
            if not token or args.ready_fd is None or args.go_fd is None:
                raise ValueError("resume requires parent-owned token and barrier pipes")
            barrier("admitted")
            if args.guard:
                lock = (root / "resume.lock").open("a+")
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    barrier("owner")
                except BlockingIOError:
                    barrier("blocked")
                    fcntl.flock(lock, fcntl.LOCK_EX)
        attempt = destination.begin_attempt()
        provider_setup(root)
        with SqliteSaver.from_conn_string(str(root / "graph.sqlite3")) as saver:
            graph = graph_for(
                saver,
                root,
                args.mode,
                token,
                attempt,
                probe,
                barrier if args.resume_root and not args.guard else None,
            )
            loaded = snapshot(graph)
            require_identity(loaded)
            write_json(
                root / f"graph-{token}-start.json",
                {
                    "schema_version": 1,
                    "pid": os.getpid(),
                    "token": token,
                    "attempt": attempt,
                    "read": loaded,
                },
            )
            graph.invoke(
                None if loaded else {"ticket_id": TICKET["id"]},
                {
                    **CONFIG,
                    "metadata": {"writer_token": token},
                },
                durability="sync",
            )
            final = snapshot(graph)
            require_identity(final)
            if final["next"]:
                raise ValueError("graph did not reach a terminal checkpoint")
            phase = final["values"]["phase"]
            visible_status(root, phase)
            destination.finish("failed" if phase == "reconciliation_hold" else "succeeded")
            write_json(
                root / f"graph-{token}-done.json",
                {
                    "schema_version": 1,
                    "pid": os.getpid(),
                    "token": token,
                    "attempt": attempt,
                    "read": final,
                },
            )
    finally:
        if lock:
            lock.close()


if __name__ == "__main__":
    main()
