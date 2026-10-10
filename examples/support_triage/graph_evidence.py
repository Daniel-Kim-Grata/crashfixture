"""Parent reads for the finite StateGraph demo, separate from core ledger checks."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from graph_worker import read_graph
from worker import BUSINESS_KEY, CONFIG, ROUTE, TICKET, provider_snapshot

from crashfixture import Boundary, Status


def validate_read(value):
    if value is None:
        return
    if (
        not isinstance(value, dict)
        or set(value) != {"values", "next", "config", "writer_token", "task_errors"}
        or not isinstance(value["values"], dict)
        or set(value["values"]) != {"ticket_id", "route", "business_key", "phase"}
        or not all(isinstance(item, str) for item in value["values"].values())
        or not isinstance(value["config"], dict)
        or set(value["config"]) != {"thread_id", "checkpoint_ns", "checkpoint_id"}
        or not all(isinstance(item, str) for item in value["config"].values())
        or not value["config"]["checkpoint_id"]
        or not isinstance(value["writer_token"], str)
        or not value["writer_token"]
        or not isinstance(value["next"], list)
        or not all(isinstance(item, str) for item in value["next"])
        or not isinstance(value["task_errors"], list)
        or not all(isinstance(item, str) for item in value["task_errors"])
    ):
        raise ValueError("malformed graph snapshot")


def receipt(root, token, pid, attempt, stage):
    value = json.loads((root / f"graph-{token}-{stage}.json").read_text())
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "pid", "token", "attempt", "read"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or type(value["pid"]) is not int
        or value["pid"] != pid
        or value["token"] != token
        or type(value["attempt"]) is not int
        or value["attempt"] != attempt
    ):
        raise ValueError("graph receipt does not identify the observed child")
    validate_read(value["read"])
    return value["read"]


def physical_effects(root):
    if not (root / "provider.sqlite3").is_file():
        raise ValueError("missing provider database")
    effects = provider_snapshot(root)
    for effect in effects:
        if (
            set(effect) != {"effect_id", "operation_key", "business_key", "ticket_id", "route"}
            or type(effect["effect_id"]) is not int
            or effect["effect_id"] <= 0
            or not all(isinstance(effect[key], str) for key in set(effect) - {"effect_id"})
        ):
            raise ValueError("malformed provider row")
    return effects


def visible_status(root):
    value = json.loads((root / "workflow-status.json").read_text())
    if (
        not isinstance(value, dict)
        or set(value)
        != {"ticket_id", "phase", "business_complete", "automatic_retry_allowed", "action_required"}
        or not isinstance(value["ticket_id"], str)
        or not isinstance(value["phase"], str)
        or type(value["business_complete"]) is not bool
        or type(value["automatic_retry_allowed"]) is not bool
        or not (value["action_required"] is None or isinstance(value["action_required"], str))
    ):
        raise ValueError("malformed visible status")
    return value


def expected_status(hold):
    return {
        "ticket_id": TICKET["id"],
        "phase": "reconciliation_hold" if hold else "acknowledged",
        "business_complete": not hold,
        "automatic_retry_allowed": not hold,
        "action_required": "reconcile provider outcome before retry" if hold else None,
    }


def terminal_reasons(final, effects, ledger, phase, expected_effects):
    reasons = []
    if (
        final is None
        or final["values"]
        != {
            "ticket_id": TICKET["id"],
            "route": ROUTE,
            "business_key": BUSINESS_KEY,
            "phase": phase,
        }
        or final["config"]
        != {**CONFIG["configurable"], "checkpoint_id": final["config"]["checkpoint_id"]}
        or final["next"]
        or final["task_errors"]
    ):
        reasons.append("invalid_terminal_graph")
    if len(effects) != expected_effects:
        reasons.append("provider_effect_count")
    if any(
        effect["business_key"] != BUSINESS_KEY
        or effect["ticket_id"] != TICKET["id"]
        or effect["route"] != ROUTE
        for effect in effects
    ):
        reasons.append("unexpected_provider_effect")
    if {effect["operation_key"] for effect in effects} != {
        effect["effect_key"] for effect in ledger["effects"]
    }:
        reasons.append("provider_mirror_mismatch")
    return reasons


def inspect_graph(result, mode):
    if result.status is Status.INCONCLUSIVE:
        return {"status": "inconclusive", "reasons": list(result.reasons)}
    root = Path(result.artifact_dir)
    hold = mode == "no_lookup" and result.scenario.boundary is not Boundary.AFTER_CHECKPOINT
    phase = "reconciliation_hold" if hold else "acknowledged"
    try:
        first, restarted = result.attempts
        initial = receipt(root, first.token, first.pid, 1, "start")
        stage = "committed" if result.scenario.boundary is Boundary.AFTER_CHECKPOINT else "intent"
        persisted = receipt(root, first.token, first.pid, 1, stage)
        resumed = receipt(root, restarted.token, restarted.pid, 2, "start")
        completed = receipt(root, restarted.token, restarted.pid, 2, "done")
        if not (root / "graph.sqlite3").is_file():
            raise ValueError("missing graph database")
        final = read_graph(root)
        validate_read(final)
        effects = physical_effects(root)
        visible = visible_status(root)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, sqlite3.Error):
        return {"status": "inconclusive", "reasons": ["missing_or_invalid_graph_evidence"]}
    reasons = list(result.reasons)
    if (
        initial is not None
        or persisted is None
        or persisted["writer_token"] != first.token
        or resumed != persisted
    ):
        reasons.append("graph_resume_checkpoint_mismatch")
    if persisted is not None and (
        persisted["values"]
        != {
            "ticket_id": TICKET["id"],
            "route": ROUTE,
            "business_key": BUSINESS_KEY,
            "phase": "acknowledged" if stage == "committed" else "intent",
        }
        or persisted["next"] != (["finalize"] if stage == "committed" else ["effect"])
        or persisted["config"]
        != {**CONFIG["configurable"], "checkpoint_id": persisted["config"]["checkpoint_id"]}
        or persisted["task_errors"]
    ):
        reasons.append("unexpected_graph_boundary")
    reasons += terminal_reasons(
        final, effects, result.final_snapshot, phase, result.scenario.expected_effects
    )
    if completed != final or (final and final["writer_token"] != restarted.token):
        reasons.append("graph_completion_receipt_mismatch")
    if mode == "stable_key" and any(effect["operation_key"] != BUSINESS_KEY for effect in effects):
        reasons.append("unstable_business_key")
    if visible != expected_status(hold):
        reasons.append("visible_graph_status_mismatch")
    return {
        "status": "fail" if reasons else "pass",
        "reasons": list(dict.fromkeys(reasons)),
        "phase": phase,
        "business_complete": not hold,
        "provider_effects": len(effects),
    }
