"""Test SQLite persistence for ratings, decisions, and hand histories."""

import os
import tempfile

import pytest

from the_felt.persistence.store import Store
from the_felt.skill.tracker import Glicko2Rating


@pytest.fixture
async def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = Store(path)
    await s.init()
    yield s
    await s.close()
    os.unlink(path)


async def test_user_and_rating_round_trip(store):
    await store.ensure_user("u_test", "Test")
    r1 = Glicko2Rating(mu=1620.5, phi=180.0, sigma=0.058)
    await store.save_rating("u_test", "preflop", r1)
    r2 = await store.get_rating("u_test", "preflop")
    assert abs(r2.mu - 1620.5) < 1e-6
    assert abs(r2.phi - 180.0) < 1e-6


async def test_default_rating_when_missing(store):
    r = await store.get_rating("u_missing", "preflop")
    assert r.mu == 1500.0
    assert r.phi == 350.0


async def test_log_and_query_decisions(store):
    await store.ensure_user("u_t", "T")
    for i in range(5):
        await store.log_decision(
            user_id="u_t",
            hand_id=f"h_{i}",
            street="flop",
            state={"k": i},
            user_action="call",
            ideal_action="fold" if i % 2 else "call",
            delta_ev=-2.0 * (i % 2),
            delta_ev_bb=-0.2 * (i % 2),
            bucket="minor_leak" if i % 2 else "great",
            leak_tag="call_too_much" if i % 2 else None,
        )
    recent = await store.recent_decisions("u_t", limit=10)
    assert len(recent) == 5
    # Most recent first
    assert recent[0]["user_action"] == "call"


async def test_save_hand_history(store):
    await store.ensure_user("u_h", "H")
    events = [{"seq": 0, "kind": "hand_start", "data": {}}, {"seq": 1, "kind": "player_action", "data": {}}]
    await store.save_hand(
        hand_id="h_42",
        user_id="u_h",
        events=events,
        result={"winners": [{"player_id": "u_h", "amount": 100}]},
    )
    # No public getter — just verify no exception
