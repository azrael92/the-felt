"""Tests for Glicko-2 tracker, decision evaluator, leak detection, difficulty adapter."""

from the_felt.skill.adapter import difficulty_for
from the_felt.skill.evaluator import score_decision
from the_felt.skill.leak import detect_leaks
from the_felt.skill.tracker import Glicko2Rating, GlickoTracker
from the_felt.types import Action, ActionType
from the_felt.coach.analyzer import DecisionContext


def test_glicko_unchanged_on_ideal_play():
    r = Glicko2Rating()
    r2 = GlickoTracker.from_delta_ev(r, delta_ev_bb=0.0)
    # Tiny drift is OK due to volatility update, but it shouldn't crash and
    # the rating shouldn't move by more than a few points.
    assert abs(r2.mu - r.mu) < 20


def test_glicko_drops_on_big_blunder():
    r = Glicko2Rating()
    r2 = GlickoTracker.from_delta_ev(r, delta_ev_bb=-5.0)
    assert r2.mu < r.mu, "blunder should reduce rating"


def test_glicko_phi_shrinks_with_data():
    r = Glicko2Rating()
    initial_phi = r.phi
    for _ in range(20):
        r = GlickoTracker.from_delta_ev(r, delta_ev_bb=-0.5)
    assert r.phi < initial_phi


def _ctx(verdict="call", ev_by_action=None, to_call=10, pot=40):
    return DecisionContext(
        hero_cards=["As", "Kh"],
        board=[],
        street="preflop",
        pot=pot,
        to_call=to_call,
        equity=0.60,
        pot_odds_required=0.25,
        edge=0.35,
        mdf=0.75,
        alpha=0.25,
        outs=0,
        rule_2_4=(0.0, 0.0),
        ev_by_action=ev_by_action or {"fold": 0.0, "call": 15.0, "raise_min": 5.0},
        verdict=verdict,
    )


def test_score_decision_blunder_fold():
    ctx = _ctx(verdict="call", ev_by_action={"fold": 0.0, "call": 15.0})
    score = score_decision(Action(ActionType.FOLD), ctx, bb=10)
    assert score.bucket in ("minor_leak", "blunder")
    assert score.leak_tag in ("fold_too_much", "fold_to_aggression")
    assert score.delta_ev_bb < 0


def test_score_decision_great_play():
    ctx = _ctx(verdict="call", ev_by_action={"fold": 0.0, "call": 15.0})
    score = score_decision(Action(ActionType.CALL, 10), ctx, bb=10)
    assert score.bucket == "great"


def test_leak_detection_finds_top():
    decisions = [
        {"bucket": "blunder", "leak_tag": "fold_too_much"},
        {"bucket": "blunder", "leak_tag": "fold_too_much"},
        {"bucket": "minor_leak", "leak_tag": "fold_too_much"},
        {"bucket": "minor_leak", "leak_tag": "call_too_much"},
        {"bucket": "great", "leak_tag": None},
        {"bucket": "fine", "leak_tag": None},
    ]
    rep = detect_leaks(decisions)
    assert rep.top_leak == "fold_too_much"
    assert rep.top_leak_count == 3
    assert rep.total_decisions == 6


def test_difficulty_band_changes_with_rating():
    p1 = difficulty_for(1000.0)
    p2 = difficulty_for(1700.0)
    p3 = difficulty_for(2200.0)
    assert p1.rating_band == "beginner"
    assert p2.rating_band == "advanced"
    assert p3.rating_band == "expert"
    assert p3.noise_multiplier < p1.noise_multiplier


def test_difficulty_exploits_leak_at_expert():
    p_default = difficulty_for(2200.0, leak_tag=None)
    p_overfold = difficulty_for(2200.0, leak_tag="fold_too_much")
    assert p_overfold.aggression_multiplier > p_default.aggression_multiplier
