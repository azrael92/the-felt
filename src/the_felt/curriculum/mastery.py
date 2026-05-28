"""Compute lesson mastery from attempt history.

Mastery rules:
- A lesson is "mastered" when accuracy ≥ 0.80 over the last 10 attempts.
- A lesson is "active" once at least one attempt exists.
- A lesson is "locked" until at least one prereq module has any attempt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from the_felt.curriculum.lessons import (
    LEAK_TO_MODULES,
    MODULES,
    all_lessons,
    get_module,
)


@dataclass(slots=True)
class LessonProgress:
    lesson_id: str
    module_id: str
    state: str           # "locked" | "active" | "mastered"
    attempts: int
    correct: int
    recent_accuracy: float


def compute_progress(
    attempts: list[dict[str, Any]],
    min_attempts: int = 10,
    target_accuracy: float = 0.80,
    window: int = 30,
) -> dict[str, LessonProgress]:
    """Given the user's drill attempts, compute per-lesson mastery state.

    `attempts` is a list of dicts each with keys: lesson_id, drill_kind, correct, ts.
    """
    # Bucket attempts by lesson_id, sorted by ts ascending
    by_lesson: dict[str, list[dict[str, Any]]] = {}
    for a in sorted(attempts, key=lambda a: a.get("ts", 0)):
        by_lesson.setdefault(a["lesson_id"], []).append(a)

    out: dict[str, LessonProgress] = {}

    # First pass: per-lesson stats
    for lesson in all_lessons():
        lst = by_lesson.get(lesson.id, [])
        attempts_n = len(lst)
        recent = lst[-window:]
        if recent:
            recent_acc = sum(1 for a in recent if a["correct"]) / len(recent)
        else:
            recent_acc = 0.0
        correct_n = sum(1 for a in lst if a["correct"])

        if attempts_n >= min_attempts and recent_acc >= target_accuracy:
            state = "mastered"
        elif attempts_n > 0:
            state = "active"
        else:
            state = "locked"
        out[lesson.id] = LessonProgress(
            lesson_id=lesson.id,
            module_id=lesson.module_id,
            state=state,
            attempts=attempts_n,
            correct=correct_n,
            recent_accuracy=recent_acc,
        )

    # Second pass: unlock lessons whose prereq module has any attempt
    for module in MODULES:
        prereq_attempted = any(
            out[l.id].attempts > 0 for prereq_id in module.prereqs
            for l in (get_module(prereq_id).lessons if get_module(prereq_id) else ())
        )
        if not module.prereqs or prereq_attempted:
            for l in module.lessons:
                if out[l.id].state == "locked":
                    # Promote to "active" (= available, not started). Keep "mastered" if so.
                    out[l.id] = LessonProgress(
                        lesson_id=l.id, module_id=l.module_id,
                        state="active" if out[l.id].attempts == 0 else out[l.id].state,
                        attempts=out[l.id].attempts,
                        correct=out[l.id].correct,
                        recent_accuracy=out[l.id].recent_accuracy,
                    )
        # If prereqs not yet attempted, lessons remain "locked"

    # The very first module always has all lessons available
    first_module = MODULES[0]
    for l in first_module.lessons:
        if out[l.id].state == "locked":
            out[l.id] = LessonProgress(
                lesson_id=l.id, module_id=l.module_id,
                state="active", attempts=0, correct=0, recent_accuracy=0.0,
            )

    return out


def recommend_next_lesson(
    progress: dict[str, LessonProgress],
    top_leak: str | None = None,
) -> str | None:
    """Pick the next lesson the user should drill.

    Strategy:
    1. If the user has a known top leak, find the first non-mastered lesson
       in a leak-recommended module.
    2. Otherwise pick the first non-mastered lesson in order.
    """
    # Leak-driven recommendation first
    if top_leak and top_leak in LEAK_TO_MODULES:
        for module_id in LEAK_TO_MODULES[top_leak]:
            module = get_module(module_id)
            if not module:
                continue
            for lesson in module.lessons:
                if progress[lesson.id].state != "mastered":
                    return lesson.id

    # Fallback: linear walk through the curriculum
    for module in MODULES:
        for lesson in module.lessons:
            if progress[lesson.id].state != "mastered":
                return lesson.id
    return None
