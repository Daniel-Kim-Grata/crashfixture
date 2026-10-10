"""A standalone callback adapter. Only a local synthetic ledger is touched."""

import os

from crashfixture import Boundary, Ledger, Probe


def main() -> None:
    destination = Ledger(os.environ["CRASHFIXTURE_DB"])
    events = Probe()
    destination.begin_attempt()
    task = destination.snapshot()["tasks"][0]
    if not task["checkpoint"]:
        if not destination.effect_exists("example-action-1"):
            events.hit(Boundary.BEFORE_DISPATCH)
            destination.commit_effect("example-action-1")
            events.hit(Boundary.AFTER_EFFECT)
        destination.checkpoint()
        events.hit(Boundary.AFTER_CHECKPOINT)
    destination.finish()


if __name__ == "__main__":
    main()
