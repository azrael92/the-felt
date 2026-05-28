"""Declarative catalog of modules and lessons.

A *module* is a topic (M1-M8). A *lesson* is one drill kind within a module.
Each lesson is a target: master it (≥ 80% accuracy across ≥ 10 attempts in
the last 30 attempts) to unlock the next.

Lessons are NOT strictly gated. After 2 correct drills in the active module,
drills from the *next* module start interleaving (~30% of the time). This
matches cog-sci consensus that interleaved practice beats blocked practice.

`relevance_for_ctx(decision_ctx)` returns the list of module IDs a live
decision spot exercises — used for live-decision drill credit and concept
focus prompts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Lesson:
    id: str                # e.g. "M2.1"
    module_id: str         # e.g. "M2"
    title: str
    drill_kind: str        # used by drills.generate(kind, rng) → Drill
    description: str
    min_attempts: int = 10
    target_accuracy: float = 0.80


@dataclass(frozen=True, slots=True)
class Module:
    id: str
    title: str
    summary: str
    lessons: tuple[Lesson, ...]
    prereqs: tuple[str, ...] = field(default_factory=tuple)
    tier_required: int = 1  # min coach-depth tier this module's drills make sense at


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

M1 = Module(
    id="M1", title="Cards & hand strength",
    summary="What's a strong hand? Read the board, rank starting hands, see the nuts.",
    lessons=(
        Lesson("M1.1", "M1", "Rank starting hands",
               "rank_starting_hands",
               "Sort 5 pre-flop hands from strongest to weakest by equity vs random."),
        Lesson("M1.2", "M1", "Best 5 of 7",
               "best_5_of_7",
               "Given your 2 cards + 5 board cards, identify your best 5-card hand."),
        Lesson("M1.3", "M1", "Range advantage",
               "range_advantage",
               "Which side does this board favor — pre-flop raiser or caller?"),
    ),
    tier_required=1,
)

M2 = Module(
    id="M2", title="Counting outs",
    summary="Cards in the deck that turn a losing hand into a winner.",
    lessons=(
        Lesson("M2.1", "M2", "Flush outs",
               "count_flush_outs",
               "How many cards complete your flush draw?"),
        Lesson("M2.2", "M2", "Open-ended straight outs",
               "count_oe_straight_outs",
               "How many cards complete an open-ended straight draw?"),
        Lesson("M2.3", "M2", "Gutshot outs",
               "count_gutshot_outs",
               "How many cards complete an inside (gutshot) straight draw?"),
        Lesson("M2.4", "M2", "Combined draws",
               "count_combo_outs",
               "Combined outs for a flush + straight draw (don't double-count)."),
    ),
    prereqs=("M1",),
    tier_required=1,
)

M3 = Module(
    id="M3", title="Rule of 2 and 4",
    summary="Turn outs into equity with a simple mental shortcut.",
    lessons=(
        Lesson("M3.1", "M3", "Rule of 2 (turn → river)",
               "outs_to_equity_turn",
               "Given N outs on the turn, what's your approximate equity by the river?"),
        Lesson("M3.2", "M3", "Rule of 4 (flop → river)",
               "outs_to_equity_river",
               "Given N outs on the flop and two cards to come, what's your approximate equity?"),
    ),
    prereqs=("M2",),
    tier_required=1,
)

M4 = Module(
    id="M4", title="Pot odds",
    summary="The minimum win % you need to make a call profitable.",
    lessons=(
        Lesson("M4.1", "M4", "Compute pot odds",
               "compute_pot_odds",
               "Given pot and call amount, what's the required win % to break even?"),
        Lesson("M4.2", "M4", "Equity vs required",
               "should_call_with_equity",
               "Compare your equity to required equity — should you call?"),
    ),
    prereqs=("M3",),
    tier_required=1,
)

M5 = Module(
    id="M5", title="Expected value (EV)",
    summary="Translate equity + pot odds into chips per decision.",
    lessons=(
        Lesson("M5.1", "M5", "EV of a call",
               "ev_call_numeric",
               "Compute the chip EV of a call given equity, pot, and call amount."),
        Lesson("M5.2", "M5", "EV of a value bet",
               "ev_bet_with_fold_equity",
               "EV of a value bet accounting for villain folding sometimes."),
        Lesson("M5.3", "M5", "Compare actions",
               "compare_ev_actions",
               "Given EVs of fold/call/raise, pick the highest-EV action."),
    ),
    prereqs=("M4",),
    tier_required=1,
)

M6 = Module(
    id="M6", title="Fold equity & bluffing",
    summary="When to bluff — and when not to. Sizing for fold equity.",
    lessons=(
        Lesson("M6.1", "M6", "Pure bluff break-even",
               "pure_bluff_break_even",
               "How often does villain need to fold for a half-pot bluff to be +EV?"),
        Lesson("M6.2", "M6", "Semi-bluff: backup equity",
               "semi_bluff_choice",
               "Choose the best semi-bluff candidate from a list of hands."),
        Lesson("M6.3", "M6", "Sizing for fold equity",
               "pick_bluff_size",
               "Which bet size maximizes fold equity given villain's calling range?"),
        Lesson("M6.4", "M6", "Should I bluff this archetype?",
               "should_bluff_archetype",
               "Given an opponent type and a spot, decide: bluff or check?"),
    ),
    prereqs=("M5",),
    tier_required=1,
)

M7 = Module(
    id="M7", title="Opponent archetypes",
    summary="Recognize the 7 main player types from their stats.",
    lessons=(
        Lesson("M7.1", "M7", "Identify the archetype",
               "identify_archetype",
               "Given VPIP/PFR/AFq stats, name the player type."),
        Lesson("M7.2", "M7", "Predict their open range",
               "predict_open_range",
               "Which hands would this archetype open from this position?"),
        Lesson("M7.3", "M7", "Predict c-bet frequency",
               "predict_cbet_freq",
               "How often will this archetype c-bet the flop?"),
    ),
    prereqs=("M6",),
    tier_required=1,
)

M8 = Module(
    id="M8", title="Exploits per archetype",
    summary="The right counter-strategy for each opponent type.",
    lessons=(
        Lesson("M8.1", "M8", "Best play vs archetype",
               "best_play_vs_archetype",
               "Given your hand and the opponent's style, pick the action."),
        Lesson("M8.2", "M8", "Bluff or value?",
               "bluff_or_value",
               "Classify the spot type — should you be bluffing or value betting?"),
    ),
    prereqs=("M7",),
    tier_required=1,
)


MODULES: tuple[Module, ...] = (M1, M2, M3, M4, M5, M6, M7, M8)


def get_module(module_id: str) -> Module | None:
    return next((m for m in MODULES if m.id == module_id), None)


def get_lesson(lesson_id: str) -> Lesson | None:
    for m in MODULES:
        for l in m.lessons:
            if l.id == lesson_id:
                return l
    return None


def all_lessons() -> list[Lesson]:
    return [l for m in MODULES for l in m.lessons]


def lessons_for(module_id: str) -> list[Lesson]:
    m = get_module(module_id)
    return list(m.lessons) if m else []


# ---------------------------------------------------------------------------
# Leak → module recommendation map
# ---------------------------------------------------------------------------
#
# When the leak detector identifies a user's top pattern, we recommend the
# corresponding module(s) to drill. Multiple modules per leak is fine — the
# recommender picks the earliest unmastered one.

LEAK_TO_MODULES: dict[str, tuple[str, ...]] = {
    "fold_too_much":         ("M4", "M5"),   # don't fold +EV calls — relearn pot odds & EV
    "fold_to_aggression":    ("M4", "M6"),   # under-defending vs bets — pot odds + bluffing math
    "call_too_much":         ("M4", "M5"),   # paying too thin — pot odds
    "fail_to_value_raise":   ("M5", "M8"),   # missing value — EV comparison + exploits
    "bluff_too_much":        ("M6",),         # over-bluffing — fold equity & sizing
    "over_aggression":       ("M6",),
    "under_aggression":      ("M5", "M6"),
    "misc":                  ("M4", "M5"),
}


# ---------------------------------------------------------------------------
# Relevance: which modules does a live decision spot exercise?
# ---------------------------------------------------------------------------

def relevance_for_ctx(ctx: dict[str, Any]) -> list[str]:
    """Given a DecisionContext-as-dict (or DecisionContext instance), return
    the list of module IDs a live decision spot exercises.

    Used to credit live `great|fine` decisions toward module mastery.
    """
    if hasattr(ctx, "__dict__"):
        d = ctx.__dict__
    elif isinstance(ctx, dict):
        d = ctx
    else:
        return []

    to_call = int(d.get("to_call", 0) or 0)
    outs = int(d.get("outs", 0) or 0)
    spot = d.get("spot") or ""
    street = d.get("street") or "preflop"

    out: list[str] = ["M1"]  # every decision exercises hand reading

    # M2/M3 — outs + rule of 2/4 relevant whenever there are outs on flop/turn
    if outs > 0 and street in ("flop", "turn"):
        out.extend(["M2", "M3"])

    # M4 — pot odds relevant any time there's a bet to face
    if to_call > 0:
        out.append("M4")

    # M5 — EV relevant for any non-trivial decision
    if to_call > 0 or spot in ("value_bet", "value_raise", "pure_bluff", "semi_bluff"):
        out.append("M5")

    # M6 — bluffing/fold-equity relevant for bluff spots
    if spot in ("pure_bluff", "semi_bluff", "bluff_catch"):
        out.append("M6")

    # M7/M8 — archetype awareness for every decision against an identified opponent
    if d.get("villain_archetype"):
        out.extend(["M7", "M8"])

    # dedupe preserving order
    seen: set[str] = set()
    ordered: list[str] = []
    for x in out:
        if x not in seen:
            seen.add(x)
            ordered.append(x)
    return ordered
