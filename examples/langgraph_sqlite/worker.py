"""Original one-action worker using the public synchronous SqliteSaver API."""

from __future__ import annotations

import argparse
import json
import os

from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.sqlite import SqliteSaver
from saver_evidence import CONFIG, EFFECT_KEY, THREAD_ID, read_checkpoint

from crashfixture import Boundary, Ledger, Probe


def write_receipt(root, probe, attempt, config, stage, observed):
    path = root / f"saver-{probe.token}-{stage}.json"
    temporary = path.with_suffix(".tmp")
    value = {
        "schema_version": 1,
        "pid": os.getpid(),
        "token": probe.token,
        "attempt": attempt,
        "thread_id": config["configurable"]["thread_id"],
        "read": observed,
    }
    with temporary.open("w") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("correct", "duplicate", "wrong_thread"))
    mode = parser.parse_args().mode
    destination = Ledger(os.environ["CRASHFIXTURE_DB"])
    root = destination.path.parent
    database = root / "saver.sqlite3"
    probe = Probe()
    attempt = destination.begin_attempt()
    thread = "wrong-synthetic-thread" if mode == "wrong_thread" and attempt > 1 else THREAD_ID
    config = {"configurable": {**CONFIG["configurable"], "thread_id": thread}}
    recovered = read_checkpoint(database, config)
    write_receipt(root, probe, attempt, config, "start", recovered)
    acknowledged = recovered is not None and recovered["channel_values"]["acknowledged"] is True
    if not acknowledged:
        if mode == "duplicate" or not destination.effect_exists(EFFECT_KEY):
            probe.hit(Boundary.BEFORE_DISPATCH)
            destination.commit_effect(probe.token if mode == "duplicate" else EFFECT_KEY)
            probe.hit(Boundary.AFTER_EFFECT)
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"] = {"effect_key": EFFECT_KEY, "acknowledged": True}
        checkpoint["channel_versions"] = {"effect_key": "1", "acknowledged": "1"}
        with SqliteSaver.from_conn_string(str(database)) as saver:
            saved_config = saver.put(
                config,
                checkpoint,
                {"source": "loop", "step": attempt, "parents": {}, "writer_token": probe.token},
                checkpoint["channel_versions"],
            )
        # A new connection observes the committed saver before the hook parks.
        observed = read_checkpoint(database, saved_config)
        write_receipt(root, probe, attempt, config, "committed", observed)
        destination.checkpoint()
        probe.hit(Boundary.AFTER_CHECKPOINT)
    destination.finish()


if __name__ == "__main__":
    main()
