"""Async SQLite store for user ratings, decisions, and hand histories.

Schema is created on first connection if missing. One database file lives
under `data/the_felt.db` (configurable via THE_FELT_DB env var).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import aiosqlite

from the_felt.skill.tracker import Glicko2Rating

log = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS ratings (
    user_id TEXT NOT NULL,
    category TEXT NOT NULL,
    mu REAL NOT NULL,
    phi REAL NOT NULL,
    sigma REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (user_id, category)
);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    hand_id TEXT NOT NULL,
    street TEXT NOT NULL,
    state_json TEXT NOT NULL,
    user_action TEXT NOT NULL,
    ideal_action TEXT NOT NULL,
    delta_ev REAL NOT NULL,
    delta_ev_bb REAL NOT NULL,
    bucket TEXT NOT NULL,
    leak_tag TEXT,
    ts REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_decisions_user_ts ON decisions(user_id, ts DESC);

CREATE TABLE IF NOT EXISTS hand_histories (
    hand_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    events_json TEXT NOT NULL,
    result_json TEXT,
    ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS streak (
    user_id TEXT PRIMARY KEY,
    current INTEGER NOT NULL DEFAULT 0,
    longest INTEGER NOT NULL DEFAULT 0,
    last_decision_ts REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS lesson_attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    lesson_id TEXT NOT NULL,
    drill_kind TEXT NOT NULL,
    correct INTEGER NOT NULL,
    ts REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attempts_user_lesson ON lesson_attempts(user_id, lesson_id, ts DESC);
"""


class Store:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def ensure_user(self, user_id: str, name: str) -> None:
        await self._conn.execute(
            "INSERT OR IGNORE INTO users (id, name, created_at) VALUES (?, ?, ?)",
            (user_id, name, time.time()),
        )
        await self._conn.commit()

    async def get_rating(self, user_id: str, category: str) -> Glicko2Rating:
        cur = await self._conn.execute(
            "SELECT mu, phi, sigma FROM ratings WHERE user_id = ? AND category = ?",
            (user_id, category),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            return Glicko2Rating()
        return Glicko2Rating(mu=row[0], phi=row[1], sigma=row[2])

    async def save_rating(self, user_id: str, category: str, rating: Glicko2Rating) -> None:
        await self._conn.execute(
            """
            INSERT INTO ratings (user_id, category, mu, phi, sigma, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, category) DO UPDATE SET
                mu = excluded.mu,
                phi = excluded.phi,
                sigma = excluded.sigma,
                updated_at = excluded.updated_at
            """,
            (user_id, category, rating.mu, rating.phi, rating.sigma, time.time()),
        )
        await self._conn.commit()

    async def log_decision(
        self,
        user_id: str,
        hand_id: str,
        street: str,
        state: dict[str, Any],
        user_action: str,
        ideal_action: str,
        delta_ev: float,
        delta_ev_bb: float,
        bucket: str,
        leak_tag: str | None,
    ) -> None:
        await self._conn.execute(
            """
            INSERT INTO decisions
              (user_id, hand_id, street, state_json, user_action, ideal_action,
               delta_ev, delta_ev_bb, bucket, leak_tag, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id, hand_id, street, json.dumps(state),
                user_action, ideal_action, delta_ev, delta_ev_bb, bucket, leak_tag,
                time.time(),
            ),
        )
        await self._conn.commit()

    async def recent_decisions(self, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        cur = await self._conn.execute(
            """
            SELECT bucket, leak_tag, delta_ev_bb, user_action, ideal_action, street, ts
            FROM decisions
            WHERE user_id = ?
            ORDER BY ts DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [
            {
                "bucket": r[0], "leak_tag": r[1], "delta_ev_bb": r[2],
                "user_action": r[3], "ideal_action": r[4], "street": r[5], "ts": r[6],
            }
            for r in rows
        ]

    async def save_hand(
        self,
        hand_id: str,
        user_id: str,
        events: list[dict[str, Any]],
        result: dict[str, Any] | None,
    ) -> None:
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO hand_histories
              (hand_id, user_id, events_json, result_json, ts)
            VALUES (?, ?, ?, ?, ?)
            """,
            (hand_id, user_id, json.dumps(events), json.dumps(result) if result else None, time.time()),
        )
        await self._conn.commit()

    # ---------- Streak ----------

    async def get_streak(self, user_id: str) -> tuple[int, int]:
        """Return (current, longest) streak for this user."""
        cur = await self._conn.execute(
            "SELECT current, longest FROM streak WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            return (0, 0)
        return (int(row[0]), int(row[1]))

    async def bump_streak(self, user_id: str) -> tuple[int, int]:
        """Increment current streak by 1; bump longest if exceeded. Returns (current, longest)."""
        current, longest = await self.get_streak(user_id)
        new_current = current + 1
        new_longest = max(longest, new_current)
        await self._conn.execute(
            """
            INSERT INTO streak (user_id, current, longest, last_decision_ts)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                current = excluded.current,
                longest = excluded.longest,
                last_decision_ts = excluded.last_decision_ts
            """,
            (user_id, new_current, new_longest, time.time()),
        )
        await self._conn.commit()
        return (new_current, new_longest)

    async def reset_streak(self, user_id: str) -> tuple[int, int]:
        """Reset current to 0 (preserving longest). Returns (0, longest)."""
        _, longest = await self.get_streak(user_id)
        await self._conn.execute(
            """
            INSERT INTO streak (user_id, current, longest, last_decision_ts)
            VALUES (?, 0, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                current = 0,
                last_decision_ts = excluded.last_decision_ts
            """,
            (user_id, longest, time.time()),
        )
        await self._conn.commit()
        return (0, longest)

    # ---------- Lesson attempts ----------

    async def record_drill_attempt(
        self,
        user_id: str,
        lesson_id: str,
        drill_kind: str,
        correct: bool,
    ) -> None:
        await self._conn.execute(
            """
            INSERT INTO lesson_attempts (user_id, lesson_id, drill_kind, correct, ts)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, lesson_id, drill_kind, 1 if correct else 0, time.time()),
        )
        await self._conn.commit()

    async def recent_attempts(
        self,
        user_id: str,
        lesson_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if lesson_id:
            cur = await self._conn.execute(
                """
                SELECT lesson_id, drill_kind, correct, ts
                FROM lesson_attempts
                WHERE user_id = ? AND lesson_id = ?
                ORDER BY ts DESC LIMIT ?
                """,
                (user_id, lesson_id, limit),
            )
        else:
            cur = await self._conn.execute(
                """
                SELECT lesson_id, drill_kind, correct, ts
                FROM lesson_attempts
                WHERE user_id = ?
                ORDER BY ts DESC LIMIT ?
                """,
                (user_id, limit),
            )
        rows = await cur.fetchall()
        await cur.close()
        return [
            {"lesson_id": r[0], "drill_kind": r[1], "correct": bool(r[2]), "ts": r[3]}
            for r in rows
        ]


_store: Store | None = None


async def get_store() -> Store:
    """Process-wide singleton store. Initializes on first call."""
    global _store
    if _store is None:
        path = os.environ.get("THE_FELT_DB", "data/the_felt.db")
        _store = Store(path)
        await _store.init()
    return _store
