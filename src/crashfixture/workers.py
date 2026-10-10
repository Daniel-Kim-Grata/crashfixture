"""Original good and deliberately broken workers; all effects are synthetic."""

from __future__ import annotations

import argparse
import os

from .ledger import TASK_ID, Ledger
from .model import Boundary
from .probe import Probe

MODES = (
    "correct",
    "duplicate",
    "forget_checkpoint",
    "lose_task",
    "retry",
    "retry_forever",
    "reset_attempts",
    "uninstrumented",
)


def work(mode: str) -> int:
    ledger = Ledger(os.environ["CRASHFIXTURE_DB"])
    probe = Probe()
    attempt = ledger.begin_attempt()
    budget = int(os.environ["CRASHFIXTURE_MAX_ATTEMPTS"])
    task = ledger.snapshot()["tasks"][0]

    if mode == "lose_task" and attempt > 1:
        ledger.finish()  # Broken: treats a restart as success with unfinished work.
        return 0
    if mode == "reset_attempts" and attempt > 1:
        with ledger._connection() as conn:
            conn.execute("UPDATE tasks SET attempts = 1 WHERE task_id = ?", (TASK_ID,))
    if task["checkpoint"] and mode != "forget_checkpoint":
        ledger.finish()
        return 0
    if mode in {"retry", "retry_forever"}:
        probe.hit(Boundary.BEFORE_DISPATCH)
        if mode == "retry" and attempt >= budget:
            ledger.finish("failed")
            return 0
        return 75  # A synthetic transient failure; no dispatch occurred.

    reconcile = mode not in {"duplicate", "forget_checkpoint"}
    if not reconcile or not ledger.effect_exists():
        if mode != "uninstrumented":
            probe.hit(Boundary.BEFORE_DISPATCH)
        ledger.commit_effect("logical-effect" if reconcile else probe.token)
        if mode != "uninstrumented":
            probe.hit(Boundary.AFTER_EFFECT)
    ledger.checkpoint()
    if mode != "uninstrumented":
        probe.hit(Boundary.AFTER_CHECKPOINT)
    ledger.finish()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=MODES)
    return work(parser.parse_args().mode)


if __name__ == "__main__":
    raise SystemExit(main())
