import sqlite3

import pytest

from crashfixture import Ledger


def test_destination_keys_dedupe_while_different_keys_reveal_duplicates(tmp_path):
    ledger = Ledger(tmp_path / "synthetic.sqlite3")
    ledger.initialize()
    ledger.begin_attempt()
    ledger.commit_effect()
    Ledger(ledger.path).commit_effect()
    assert len(ledger.snapshot()["effects"]) == 1
    assert Ledger(ledger.path).effect_exists()
    ledger.commit_effect("wrong-replay-key")
    assert len(ledger.snapshot()["effects"]) == 2
    assert ledger.snapshot()["tasks"][0]["checkpoint"] == 0
    ledger.checkpoint()
    ledger.finish()
    assert Ledger(ledger.path).snapshot()["tasks"][0]["state"] == "succeeded"


def test_committed_effects_cannot_be_updated_or_deleted_through_normal_sql(tmp_path):
    ledger = Ledger(tmp_path / "append-only.sqlite3")
    ledger.initialize()
    ledger.commit_effect()
    for sql in ("DELETE FROM effects", "UPDATE effects SET effect_key = 'replacement'"):
        with (
            pytest.raises(sqlite3.IntegrityError, match="append-only"),
            ledger._connection() as conn,
        ):
            conn.execute(sql)
    assert len(ledger.snapshot()["effects"]) == 1
