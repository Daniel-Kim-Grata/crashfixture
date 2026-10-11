"""Original synthetic charge/retry model; only local SQLite rows, no real money."""

from __future__ import annotations

import argparse
import os
import sqlite3

from crashfixture import Boundary, Ledger, Probe

OPERATION = "fixture-order-001:charge:v1"
AMOUNT_CENTS = 700


def charges(root):
    path = root / "charges.sqlite3"
    if not path.is_file():
        raise ValueError("missing synthetic charge database")
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute("SELECT * FROM charges ORDER BY charge_id")]


def dispatch(root, key):
    with sqlite3.connect(root / "charges.sqlite3") as connection:
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS charges (charge_id INTEGER PRIMARY KEY, "
            "request_key TEXT UNIQUE NOT NULL, operation TEXT NOT NULL, "
            "amount_cents INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT OR IGNORE INTO charges(request_key,operation,amount_cents) VALUES (?,?,?)",
            (key, OPERATION, AMOUNT_CENTS),
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("unsafe", "stable_key"))
    mode = parser.parse_args().mode
    ledger = Ledger(os.environ["CRASHFIXTURE_DB"])
    root = ledger.path.parent
    probe = Probe()
    ledger.begin_attempt()
    if not ledger.snapshot()["tasks"][0]["checkpoint"]:
        key = OPERATION if mode == "stable_key" else probe.token
        probe.hit(Boundary.BEFORE_DISPATCH)
        dispatch(root, key)
        # Separate cooperative mirror: the commit-to-mirror gap is outside this fixture.
        ledger.commit_effect(key)
        probe.hit(Boundary.AFTER_EFFECT)
        ledger.checkpoint()
        probe.hit(Boundary.AFTER_CHECKPOINT)
    ledger.finish()


if __name__ == "__main__":
    main()
