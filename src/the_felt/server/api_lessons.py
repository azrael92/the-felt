"""HTTP endpoints for the lesson catalog and per-user progress.

The catalog itself is static config (no per-session state) so HTTP is the
right transport — cacheable, easy to inspect from a browser tab.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter

from the_felt.curriculum.lessons import MODULES
from the_felt.curriculum.mastery import compute_progress
from the_felt.curriculum.stages import STAGES, get_walkthrough
from the_felt.persistence.store import get_store

router = APIRouter()


@router.get("/api/stages")
async def stages_catalog():
    """Return the 8 training stages and their walkthroughs (static config)."""
    return {
        "stages": [
            {
                "id": s.id,
                "title": s.title,
                "teaches": s.teaches,
                "intro": s.intro,
                "walkthrough": get_walkthrough(s.id),
            }
            for s in STAGES
        ]
    }


@router.get("/api/lessons")
async def lesson_catalog():
    """Return the static module/lesson catalog."""
    return {
        "modules": [
            {
                "id": m.id,
                "title": m.title,
                "summary": m.summary,
                "prereqs": list(m.prereqs),
                "tier_required": m.tier_required,
                "lessons": [
                    {
                        "id": l.id,
                        "module_id": l.module_id,
                        "title": l.title,
                        "drill_kind": l.drill_kind,
                        "description": l.description,
                        "min_attempts": l.min_attempts,
                        "target_accuracy": l.target_accuracy,
                    }
                    for l in m.lessons
                ],
            }
            for m in MODULES
        ]
    }


@router.get("/api/users/{user_id}/progress")
async def user_progress(user_id: str):
    """Return mastery state for every lesson for this user."""
    store = await get_store()
    attempts = await store.recent_attempts(user_id, limit=500)
    progress = compute_progress(attempts)
    return {
        "user_id": user_id,
        "progress": {
            lesson_id: {
                "lesson_id": p.lesson_id,
                "module_id": p.module_id,
                "state": p.state,
                "attempts": p.attempts,
                "correct": p.correct,
                "recent_accuracy": round(p.recent_accuracy, 3),
            }
            for lesson_id, p in progress.items()
        },
    }
