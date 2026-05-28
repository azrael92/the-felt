"""Tests for the pilot-style training stage state machine."""

import random

from the_felt.curriculum.stages import (
    STAGES,
    StageState,
    build_quiz_for_decision,
    get_stage,
    grade_answer,
    is_meaningful_decision,
    next_stage,
    should_quiz,
)


def test_eight_stages_defined():
    assert len(STAGES) == 8
    assert STAGES[0].id == 1
    assert STAGES[-1].id == 8


def test_initial_state_quizzes_every_turn():
    s = StageState()
    assert s.frequency() == 1.0
    assert not s.ready_to_graduate()


def test_frequency_drops_with_streak():
    s = StageState()
    for _ in range(5):
        s.record_quiz_result(True)
    assert s.frequency() == 0.66
    for _ in range(5):
        s.record_quiz_result(True)
    assert s.frequency() == 0.33
    for _ in range(5):
        s.record_quiz_result(True)
    assert s.frequency() == 0.20


def test_wrong_answer_resets_frequency_to_max():
    s = StageState()
    for _ in range(12):
        s.record_quiz_result(True)
    assert s.frequency() == 0.33
    s.record_quiz_result(False)
    assert s.frequency() == 1.0
    assert s.correct_streak == 0


def test_clean_hands_counter_bumps_on_perfect_hand():
    s = StageState()
    s.record_quiz_result(True)
    s.record_quiz_result(True)
    # End of hand 1 — all answers correct → clean_hands bumps to 1
    s.reset_hand()
    assert s.clean_hands == 1
    # Hand 2 with one wrong answer
    s.record_quiz_result(True)
    s.record_quiz_result(False)
    s.reset_hand()
    # clean_hands stays 0 (reset by wrong answer)
    assert s.clean_hands == 0


def test_ready_to_graduate_requires_low_freq_and_clean_hands():
    s = StageState()
    # Just streaks of correct answers alone aren't enough — need clean hands AND
    # enough quizzes attempted.
    for _ in range(12):
        s.record_quiz_result(True)
    # Need 5 clean hands too
    assert not s.ready_to_graduate()
    # Simulate 5 perfect hands
    for _ in range(5):
        s.this_hand_clean = True
        s.reset_hand()
    assert s.ready_to_graduate()


def test_is_meaningful_decision_skips_trivial_spots():
    # Pre-flop fold with no bet to call and no value spot
    assert not is_meaningful_decision({"to_call": 0, "outs": 0, "spot": "give_up"})
    # Bet to face → meaningful
    assert is_meaningful_decision({"to_call": 25, "outs": 0, "spot": "bluff_catch"})
    # Outs → meaningful
    assert is_meaningful_decision({"to_call": 0, "outs": 9, "spot": "marginal"})
    # Value bet spot → meaningful
    assert is_meaningful_decision({"to_call": 0, "outs": 0, "spot": "value_bet"})


def test_should_quiz_respects_meaningful_check():
    rng = random.Random(0)
    state = StageState()
    # Trivial spot → never quiz regardless of frequency
    assert not should_quiz(state, {"to_call": 0, "outs": 0, "spot": "give_up"}, rng)


def test_build_quiz_includes_correct_answer_for_grading():
    state = StageState(stage_id=2)
    ctx = {"to_call": 10, "outs": 9, "spot": "bluff_catch", "street": "flop"}
    quiz = build_quiz_for_decision(state, ctx)
    assert quiz["stage_id"] == 2
    assert len(quiz["questions"]) == 1
    assert quiz["questions"][0]["id"] == "outs"
    assert quiz["questions"][0]["correct"] == 9


def test_grade_answer_numeric_tolerance():
    q = {"answer_type": "numeric", "correct": 28.6, "tolerance": 3.0}
    assert grade_answer(q, 28.6)
    assert grade_answer(q, 30.0)
    assert grade_answer(q, 26.0)
    assert not grade_answer(q, 35.0)


def test_grade_answer_mc_index():
    q = {"answer_type": "mc", "correct": 2, "tolerance": 0}
    assert grade_answer(q, 2)
    assert not grade_answer(q, 1)


def test_next_stage_walks_through_curriculum():
    for stage in STAGES[:-1]:
        nxt = next_stage(stage.id)
        assert nxt is not None
        assert nxt.id == stage.id + 1
    # Last stage has no next
    assert next_stage(STAGES[-1].id) is None
