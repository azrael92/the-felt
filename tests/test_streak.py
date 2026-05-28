"""Streak tracking invariants:

- 'great'  → increment (+1)
- 'fine'   → increment (+1)
- 'minor_leak' → no-op (preserves streak on borderline spots)
- 'blunder'   → reset to 0
- Capped at 1 increment per hand (anti-gaming)
- Persisted server-side per user
"""

import os
import tempfile

import pytest

from the_felt.persistence.store import Store


@pytest.fixture
async def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = Store(path)
    await s.init()
    yield s
    await s.close()
    os.unlink(path)


async def test_streak_default_zero(store):
    cur, longest = await store.get_streak("u_new")
    assert cur == 0
    assert longest == 0


async def test_bump_increments_current_and_longest(store):
    cur, longest = await store.bump_streak("u")
    assert cur == 1
    assert longest == 1
    cur, longest = await store.bump_streak("u")
    assert cur == 2
    assert longest == 2


async def test_reset_zeroes_current_preserves_longest(store):
    for _ in range(5):
        await store.bump_streak("u")
    cur, longest = await store.get_streak("u")
    assert cur == 5
    assert longest == 5
    cur, longest = await store.reset_streak("u")
    assert cur == 0
    assert longest == 5


async def test_longest_survives_resets(store):
    await store.bump_streak("u")
    await store.bump_streak("u")
    await store.bump_streak("u")
    await store.reset_streak("u")
    await store.bump_streak("u")
    cur, longest = await store.get_streak("u")
    assert cur == 1
    assert longest == 3


async def test_drill_attempts_round_trip(store):
    await store.record_drill_attempt("u", "M2", "count_flush_outs", True)
    await store.record_drill_attempt("u", "M2", "count_flush_outs", False)
    await store.record_drill_attempt("u", "M3", "outs_to_equity_river", True)
    m2 = await store.recent_attempts("u", lesson_id="M2")
    assert len(m2) == 2
    all_attempts = await store.recent_attempts("u")
    assert len(all_attempts) == 3
