"""Curriculum tests: drill generation, oracle agreement, mastery computation."""

import random

import pytest

from the_felt.curriculum.drills import GENERATORS, generate, is_correct
from the_felt.curriculum.lessons import (
    LEAK_TO_MODULES,
    MODULES,
    all_lessons,
    get_lesson,
    get_module,
    relevance_for_ctx,
)
from the_felt.curriculum.mastery import compute_progress, recommend_next_lesson


def test_module_catalog_well_formed():
    assert len(MODULES) == 8
    ids = [m.id for m in MODULES]
    assert ids == ["M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8"]
    # Every module has at least one lesson
    for m in MODULES:
        assert len(m.lessons) >= 1
        for l in m.lessons:
            assert l.module_id == m.id
            assert l.id.startswith(m.id + ".")


def test_leak_to_modules_maps_to_valid_modules():
    valid_ids = {m.id for m in MODULES}
    for leak, mods in LEAK_TO_MODULES.items():
        for mid in mods:
            assert mid in valid_ids, f"{leak} → {mid} but {mid} doesn't exist"


def test_relevance_for_ctx_postflop_with_draws():
    ctx = {
        "to_call": 25, "outs": 9, "spot": "semi_bluff",
        "street": "flop", "villain_archetype": "TAG",
    }
    rel = relevance_for_ctx(ctx)
    # Outs + bet → M2, M3, M4, M5. Semi-bluff → M6. Archetype known → M7, M8.
    assert "M2" in rel
    assert "M3" in rel
    assert "M4" in rel
    assert "M5" in rel
    assert "M6" in rel
    assert "M7" in rel
    assert "M8" in rel


def test_relevance_for_ctx_preflop_no_bet():
    ctx = {"to_call": 0, "outs": 0, "spot": "value_bet", "street": "preflop"}
    rel = relevance_for_ctx(ctx)
    # Always M1; M5 because there's a value-bet spot
    assert "M1" in rel
    assert "M5" in rel
    # No M2/M3 (no outs preflop)
    assert "M2" not in rel
    assert "M3" not in rel


@pytest.mark.parametrize("kind", list(GENERATORS.keys()))
def test_every_drill_kind_generates_valid_drill(kind):
    rng = random.Random(42)
    drill = generate(kind, rng)
    assert drill.kind == kind
    assert drill.question
    assert drill.answer_type in ("mc", "numeric", "ordered")
    if drill.answer_type == "mc":
        assert len(drill.choices) >= 2
        assert 0 <= drill.correct_index < len(drill.choices)
    assert drill.explanation


def test_drill_correct_answer_grades_correct_mc():
    drill = generate("count_flush_outs", random.Random(7))
    assert drill.answer_type == "mc"
    assert is_correct(drill, drill.correct_index)
    # Wrong index = wrong
    wrong_idx = (drill.correct_index + 1) % len(drill.choices)
    assert not is_correct(drill, wrong_idx)


def test_drill_correct_answer_grades_numeric_tolerance():
    drill = generate("compute_pot_odds", random.Random(7))
    assert drill.answer_type == "numeric"
    # Exact answer is within tolerance
    assert is_correct(drill, drill.answer)
    # Way off is not
    assert not is_correct(drill, drill.answer + 50)


def test_mastery_empty_user_all_locked_except_m1():
    progress = compute_progress([])
    # M1 lessons should be active (no prereqs); others locked
    for l in get_module("M1").lessons:
        assert progress[l.id].state == "active"
    for l in get_module("M2").lessons:
        assert progress[l.id].state == "locked"


def test_mastery_completes_with_enough_correct_attempts():
    # 10 correct attempts on a lesson → mastered
    attempts = [
        {"lesson_id": "M2.1", "drill_kind": "count_flush_outs", "correct": True, "ts": i}
        for i in range(10)
    ]
    progress = compute_progress(attempts)
    assert progress["M2.1"].state == "mastered"


def test_mastery_unlocks_next_module_after_any_attempt():
    # One attempt on M1 unlocks M2 lessons
    attempts = [{"lesson_id": "M1.1", "drill_kind": "rank_starting_hands", "correct": False, "ts": 0}]
    progress = compute_progress(attempts)
    assert progress["M2.1"].state == "active"


def test_recommend_next_lesson_uses_leak():
    progress = compute_progress([])
    # fold_too_much → M4 first
    next_id = recommend_next_lesson(progress, top_leak="fold_too_much")
    assert next_id is not None and next_id.startswith("M4")
