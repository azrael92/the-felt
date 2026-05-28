"""Decision evaluator: scores the user's action vs. the EV-maximizing benchmark.

Output is a `DecisionScore` with:
- delta_ev: EV(user_action) - EV(ideal_action)
- delta_ev_bb: delta_ev / bb (pot-normalized for fair comparison)
- bucket: one of "great" | "fine" | "minor_leak" | "blunder"
- leak_tag: a short categorization of WHAT went wrong, used for leak detection
"""

from __future__ import annotations

from dataclasses import dataclass

from the_felt.coach.analyzer import DecisionContext
from the_felt.types import Action, ActionType


@dataclass(slots=True)
class DecisionScore:
    user_action: str
    ideal_action: str
    user_ev: float
    ideal_ev: float
    delta_ev: float
    delta_ev_bb: float
    bucket: str          # "great" | "fine" | "minor_leak" | "blunder"
    leak_tag: str | None  # e.g. "fold_too_much", "call_too_much", "bluff_too_much"


def score_decision(
    user_action: Action,
    ctx: DecisionContext,
    bb: int,
) -> DecisionScore:
    """Score the user's action against the EV-max benchmark from ctx."""
    user_key = _action_key(user_action, ctx)
    ideal_key = ctx.verdict or _best_key(ctx)

    user_ev = ctx.ev_by_action.get(user_key, 0.0)
    ideal_ev = ctx.ev_by_action.get(ideal_key, user_ev)
    delta_ev = user_ev - ideal_ev
    delta_ev_bb = delta_ev / max(bb, 1)

    bucket = _bucket(delta_ev_bb)
    leak_tag = _classify_leak(user_key, ideal_key, ctx) if bucket in ("minor_leak", "blunder") else None

    return DecisionScore(
        user_action=user_key,
        ideal_action=ideal_key,
        user_ev=user_ev,
        ideal_ev=ideal_ev,
        delta_ev=delta_ev,
        delta_ev_bb=delta_ev_bb,
        bucket=bucket,
        leak_tag=leak_tag,
    )


def _action_key(action: Action, ctx: DecisionContext) -> str:
    """Map an Action to the same key shape the analyzer uses in ev_by_action."""
    t = action.type
    if t == ActionType.FOLD: return "fold"
    if t == ActionType.CHECK: return "check"
    if t == ActionType.CALL: return "call"
    if t == ActionType.BET: return "bet_value"
    if t == ActionType.RAISE:
        if "raise_big" in ctx.ev_by_action and action.amount > 0:
            min_ev = ctx.ev_by_action.get("raise_min", 0.0)
            big_ev = ctx.ev_by_action.get("raise_big", min_ev)
            return "raise_big" if big_ev > min_ev else "raise_min"
        return "raise_min"
    if t == ActionType.ALL_IN: return "raise_big"
    return "fold"


def _best_key(ctx: DecisionContext) -> str:
    if not ctx.ev_by_action:
        return "fold"
    return max(ctx.ev_by_action, key=lambda k: ctx.ev_by_action[k])


def _bucket(delta_ev_bb: float) -> str:
    # Positive delta means user found a better play than benchmark — possible
    # since our benchmark is heuristic-grade. Treat as "great".
    if delta_ev_bb >= -0.1:
        return "great"
    if delta_ev_bb >= -0.5:
        return "fine"
    if delta_ev_bb >= -2.0:
        return "minor_leak"
    return "blunder"


def _classify_leak(user: str, ideal: str, ctx: DecisionContext) -> str | None:
    """Heuristic tag describing the leak type. Used for leak detection."""
    raises = ("raise_min", "raise_big", "bet_value")
    if user == "fold" and ideal in ("call", *raises):
        if ctx.to_call > 0 and ctx.edge > 0:
            return "fold_too_much"
        return "fold_to_aggression"
    if user == "call" and ideal == "fold":
        return "call_too_much"
    if user == "call" and ideal in raises:
        return "fail_to_value_raise"
    if user in raises and ideal == "fold":
        return "bluff_too_much"
    if user in raises and ideal == "check":
        return "over_aggression"
    if user == "check" and ideal in raises:
        return "under_aggression"
    return "misc"
