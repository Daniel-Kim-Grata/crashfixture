"""Independent parent reads of this synthetic workflow's saved evidence."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from worker import BUSINESS_KEY, CONFIG, ROUTE, TICKET, provider_snapshot, read_saver

from crashfixture import Boundary, Status


def validate_read(value):
    if value is None:
        return
    if (
        not isinstance(value, dict)
        or set(value) != {"checkpoint_id", "config", "channels", "writer_token"}
        or not isinstance(value["config"], dict)
        or set(value["config"]) != {"thread_id", "checkpoint_ns", "checkpoint_id"}
        or not all(isinstance(item, str) for item in value["config"].values())
        or not isinstance(value["channels"], dict)
        or set(value["channels"]) != {"ticket_id", "route", "business_key", "phase"}
        or not all(isinstance(item, str) for item in value["channels"].values())
        or not isinstance(value["checkpoint_id"], str)
        or not value["checkpoint_id"]
        or value["config"]["checkpoint_id"] != value["checkpoint_id"]
        or not isinstance(value["writer_token"], str)
        or not value["writer_token"]
    ):
        raise ValueError("malformed saver evidence")


def receipt(root, attempt, stage):
    value = json.loads((root / f"triage-{attempt.token}-{stage}.json").read_text())
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "pid", "token", "attempt", "read"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or type(value["pid"]) is not int
        or value["pid"] != attempt.pid
        or value["token"] != attempt.token
        or type(value["attempt"]) is not int
        or value["attempt"] != attempt.number
    ):
        raise ValueError("receipt does not identify the observed child")
    validate_read(value["read"])
    return value["read"]


def inspect_workflow(result, mode):
    if result.status is Status.INCONCLUSIVE:
        return {"status": "inconclusive", "reasons": list(result.reasons)}
    root = Path(result.artifact_dir)
    boundary = result.scenario.boundary
    hold = mode == "no_lookup" and boundary is not Boundary.AFTER_CHECKPOINT
    phase = "reconciliation_hold" if hold else "acknowledged"
    visible_phase = "stalled" if mode == "stalled" else phase
    try:
        first, restarted = result.attempts
        initial = receipt(root, first, "start")
        resumed = receipt(root, restarted, "start")
        stage = "committed" if boundary is Boundary.AFTER_CHECKPOINT else "intent"
        persisted = receipt(root, first, stage)
        writer = first if boundary is Boundary.AFTER_CHECKPOINT else restarted
        committed = receipt(root, writer, "committed")
        if not (root / "saver.sqlite3").is_file() or not (root / "provider.sqlite3").is_file():
            raise ValueError("missing durable database")
        final = read_saver(root)
        validate_read(final)
        effects = provider_snapshot(root)
        for effect in effects:
            if (
                set(effect) != {"effect_id", "operation_key", "business_key", "ticket_id", "route"}
                or type(effect["effect_id"]) is not int
                or effect["effect_id"] <= 0
                or not all(isinstance(effect[key], str) for key in set(effect) - {"effect_id"})
            ):
                raise ValueError("malformed provider evidence")
        visible = json.loads((root / "workflow-status.json").read_text())
        if (
            not isinstance(visible, dict)
            or set(visible)
            != {
                "ticket_id",
                "phase",
                "business_complete",
                "automatic_retry_allowed",
                "action_required",
            }
            or type(visible["business_complete"]) is not bool
            or type(visible["automatic_retry_allowed"]) is not bool
            or not isinstance(visible["ticket_id"], str)
            or not isinstance(visible["phase"], str)
            or (
                visible["action_required"] is not None
                and not isinstance(visible["action_required"], str)
            )
        ):
            raise ValueError("malformed visible status")
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, sqlite3.Error):
        return {"status": "inconclusive", "reasons": ["missing_or_invalid_workflow_evidence"]}

    reasons = list(result.reasons)
    if initial is not None:
        reasons.append("unexpected_initial_checkpoint")
    if (
        persisted is None
        or persisted["writer_token"] != first.token
        or persisted["channels"]
        != {
            "ticket_id": TICKET["id"],
            "route": ROUTE,
            "business_key": BUSINESS_KEY,
            "phase": "acknowledged" if boundary is Boundary.AFTER_CHECKPOINT else "intent",
        }
        or persisted["config"]
        != {**CONFIG["configurable"], "checkpoint_id": persisted["checkpoint_id"]}
        or resumed != persisted
    ):
        reasons.append("resume_checkpoint_mismatch")
    expected_channels = {
        "ticket_id": TICKET["id"],
        "route": ROUTE,
        "business_key": BUSINESS_KEY,
        "phase": phase,
    }
    if (
        committed is None
        or committed["channels"] != expected_channels
        or committed["writer_token"] != writer.token
        or committed["config"]
        != {**CONFIG["configurable"], "checkpoint_id": committed["checkpoint_id"]}
        or final != committed
    ):
        reasons.append("final_checkpoint_mismatch")
    if any(
        effect["business_key"] != BUSINESS_KEY
        or effect["ticket_id"] != TICKET["id"]
        or effect["route"] != ROUTE
        for effect in effects
    ):
        reasons.append("unexpected_provider_effect")
    if {effect["operation_key"] for effect in effects} != {
        effect["effect_key"] for effect in result.final_snapshot["effects"]
    }:
        reasons.append("provider_mirror_mismatch")
    if mode in {"stable_key", "stalled"} and any(
        effect["operation_key"] != BUSINESS_KEY for effect in effects
    ):
        reasons.append("unstable_business_operation_key")
    if len(effects) != result.scenario.expected_effects:
        reasons.append("provider_effect_count")
    complete = visible_phase == "acknowledged"
    action = (
        None
        if complete
        else "reconcile provider outcome before retry"
        if hold
        else "investigate stalled recovery"
    )
    if visible != {
        "ticket_id": TICKET["id"],
        "phase": visible_phase,
        "business_complete": complete,
        "automatic_retry_allowed": complete,
        "action_required": action,
    }:
        reasons.append("visible_status_mismatch")
    if mode == "stalled":
        reasons.append("visible_stalled_run")
    return {
        "status": "fail" if reasons else "pass",
        "reasons": list(dict.fromkeys(reasons)),
        "workflow_phase": visible["phase"],
        "business_complete": visible["business_complete"],
        "provider_effects": len(effects),
    }
