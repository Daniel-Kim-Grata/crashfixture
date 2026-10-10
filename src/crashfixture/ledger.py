"""A destination-side synthetic ledger, separate from durable task checkpoints.

Nothing here performs a live external effect. Each operation commits before it
returns; connections are short-lived so a kill never relies on Python cleanup.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

TASK_ID = "synthetic-task"


class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=1.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA synchronous = FULL")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE tasks (
                    task_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL CHECK(state IN ('pending','running','succeeded','failed')),
                    attempts INTEGER NOT NULL CHECK(attempts >= 0),
                    checkpoint INTEGER NOT NULL CHECK(checkpoint IN (0,1))
                );
                CREATE TABLE effects (
                    effect_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    effect_key TEXT NOT NULL,
                    payload TEXT NOT NULL CHECK(payload = 'synthetic-effect'),
                    UNIQUE(task_id, effect_key)
                );
                CREATE TRIGGER effects_no_delete BEFORE DELETE ON effects
                BEGIN SELECT RAISE(ABORT, 'synthetic effects are append-only'); END;
                CREATE TRIGGER effects_no_update BEFORE UPDATE ON effects
                BEGIN SELECT RAISE(ABORT, 'synthetic effects are append-only'); END;
                """
            )
            conn.execute("INSERT INTO tasks VALUES (?, 'pending', 0, 0)", (TASK_ID,))

    def begin_attempt(self) -> int:
        with self._connection() as conn:
            conn.execute(
                "UPDATE tasks SET attempts = attempts + 1, state = 'running' WHERE task_id = ?",
                (TASK_ID,),
            )
            return int(
                conn.execute("SELECT attempts FROM tasks WHERE task_id = ?", (TASK_ID,)).fetchone()[
                    0
                ]
            )

    def effect_exists(self, key: str = "logical-effect") -> bool:
        with self._connection() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM effects WHERE task_id = ? AND effect_key = ?", (TASK_ID, key)
                ).fetchone()
                is not None
            )

    def commit_effect(self, key: str = "logical-effect") -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO effects(task_id, effect_key, payload) VALUES (?, ?, ?)",
                (TASK_ID, key, "synthetic-effect"),
            )

    def checkpoint(self) -> None:
        with self._connection() as conn:
            conn.execute("UPDATE tasks SET checkpoint = 1 WHERE task_id = ?", (TASK_ID,))

    def finish(self, state: str = "succeeded") -> None:
        if state not in {"succeeded", "failed"}:
            raise ValueError("terminal state required")
        with self._connection() as conn:
            conn.execute("UPDATE tasks SET state = ? WHERE task_id = ?", (state, TASK_ID))

    def snapshot(self) -> dict[str, Any]:
        with self._connection() as conn:
            # One read transaction binds task state and destination evidence.
            conn.execute("BEGIN")
            tasks = conn.execute("SELECT * FROM tasks ORDER BY task_id").fetchall()
            effects = conn.execute(
                "SELECT effect_id, task_id, effect_key, payload FROM effects ORDER BY effect_id"
            ).fetchall()
            return {"tasks": [dict(t) for t in tasks], "effects": [dict(e) for e in effects]}
