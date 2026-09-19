"""Every attempt, on the record.

A booking that was missed must be explainable afterwards - which attempt fired
when, what the server said, how far the clock had drifted. Without that, a loss is
indistinguishable from a bug, and the next fix is a guess.

SQLite rather than the assistant's Postgres on purpose: this runs on a mini-PC as
a single writer with no concurrent readers, and the racer must not acquire a
booking and then fail to record it because a database on another host blinked.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS races (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id       TEXT    NOT NULL,
    class_label    TEXT    NOT NULL DEFAULT '',
    t0             REAL    NOT NULL,
    clock_offset   REAL    NOT NULL DEFAULT 0,
    clock_disp     REAL    NOT NULL DEFAULT 0,
    registered     INTEGER NOT NULL DEFAULT 0,
    verified       INTEGER NOT NULL DEFAULT 0,
    seat           INTEGER,
    reason         TEXT    NOT NULL DEFAULT '',
    max_overshoot_ms REAL  NOT NULL DEFAULT 0,
    created_at     TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id   INTEGER NOT NULL REFERENCES races(id) ON DELETE CASCADE,
    seq       INTEGER NOT NULL,
    outcome   TEXT    NOT NULL,
    status    INTEGER,
    latency   REAL    NOT NULL,
    fired_at  REAL    NOT NULL,
    detail    TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS attempts_race ON attempts(race_id);
-- One race per class, so a retry or a restart can never double-book.
CREATE UNIQUE INDEX IF NOT EXISTS races_class_won
    ON races(class_id) WHERE registered = 1;
"""


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    def already_won(self, class_id: str) -> bool:
        """Has this class already been booked? Guards against a restart re-racing."""
        row = self._conn.execute(
            "SELECT 1 FROM races WHERE class_id = ? AND registered = 1 LIMIT 1", (class_id,)
        ).fetchone()
        return row is not None

    def record(self, *, plan: Any, result: Any, clock_offset: float, clock_disp: float) -> int:
        """Persist a finished race and its attempts as one unit.

        Race and attempts go in a single transaction: a race row without its
        attempts is exactly the half-record that makes a post-mortem impossible.
        """
        with self.transaction() as conn:
            cur = conn.execute(
                """INSERT INTO races (class_id, class_label, t0, clock_offset, clock_disp,
                                      registered, verified, seat, reason, max_overshoot_ms, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    plan.class_id,
                    plan.label,
                    plan.t0,
                    clock_offset,
                    clock_disp,
                    int(result.registered),
                    int(result.verified),
                    result.seat,
                    result.reason,
                    result.max_overshoot_ms,
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                ),
            )
            race_id = int(cur.lastrowid or 0)
            conn.executemany(
                """INSERT INTO attempts (race_id, seq, outcome, status, latency, fired_at, detail)
                   VALUES (?,?,?,?,?,?,?)""",
                [
                    (race_id, i, a.outcome.value, a.status, a.latency, a.fired_at, a.detail)
                    for i, a in enumerate(result.attempts, start=1)
                ],
            )
        return race_id

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM races ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def attempts_for(self, race_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM attempts WHERE race_id = ? ORDER BY seq", (race_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def json_default(obj: Any) -> Any:  # pragma: no cover - serialisation helper
    if hasattr(obj, "value"):
        return obj.value
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    return json.JSONEncoder().default(obj)
