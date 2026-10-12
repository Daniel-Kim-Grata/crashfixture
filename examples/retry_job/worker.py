"""Synthetic queued report job; output is one local SQLite row, not a real report."""

import argparse
import os

from crashfixture import Boundary, Ledger, Probe

JOB_ID = "weekly-report-001"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("unsafe", "stable_key"))
    mode = parser.parse_args().mode
    destination = Ledger(os.environ["CRASHFIXTURE_DB"])
    probe = Probe()
    destination.begin_attempt()
    task = destination.snapshot()["tasks"][0]
    if not task["checkpoint"]:
        key = f"report:{JOB_ID}"
        if mode == "unsafe":
            key += f":attempt:{probe.token}"
        if not destination.effect_exists(key):
            probe.hit(Boundary.BEFORE_DISPATCH)
            destination.commit_effect(key)
            probe.hit(Boundary.AFTER_EFFECT)
        destination.checkpoint()
        probe.hit(Boundary.AFTER_CHECKPOINT)
    destination.finish()


if __name__ == "__main__":
    main()
