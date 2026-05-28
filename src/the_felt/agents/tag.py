"""Phase-1 TAG bot.

A single rule-based decision function good enough to play reasonable poker.
Phase 2 will replace this with the full archetype-parameterized engine.
"""

from __future__ import annotations

import random

from the_felt.agents.hand_strength import preflop_strength_tier_fast
from the_felt.engine.hand import ActionRequest, Hand
from the_felt.engine.table import Player
from the_felt.equity.monte_carlo import equity_vs_random
from the_felt.probability.pot_odds import pot_odds
from the_felt.types import Action, ActionType, Position, Street


# Coarse position categorization for a 6-max-ish opening strategy.
_LATE = {Position.BTN, Position.CO, Position.HJ}
_MIDDLE = {Position.LJ, Position.MP, Position.UTG1}
_EARLY = {Position.UTG}
_BLINDS = {Position.SB, Position.BB}


def decide_tag(hand: Hand, req: ActionRequest, rng: random.Random | None = None) -> Action:
    """Pick an action for a TAG bot facing `req` in `hand`."""
    rng = rng or random.Random()
    player = hand.table.players[req.seat]

    if req.street == Street.PREFLOP:
        return _preflop(hand, player, req, rng)
    return _postflop(hand, player, req, rng)


# ---------- preflop ----------


def _preflop(hand: Hand, player: Player, req: ActionRequest, rng: random.Random) -> Action:
    tier = preflop_strength_tier_fast(player.hole_cards)
    pos = player.position
    facing_raise = req.to_call > hand.table.bb

    if not facing_raise:
        # First in (or limped pot)
        if tier == "premium":
            return _raise_to(hand, req, mult=3.0)
        if tier == "strong":
            if pos in _EARLY:
                return _maybe_fold_or_call(req)
            return _raise_to(hand, req, mult=2.5)
        if tier == "decent":
            if pos in _LATE:
                return _raise_to(hand, req, mult=2.5)
            if pos in _BLINDS and req.to_call <= hand.table.bb:
                return Action(ActionType.CALL, req.to_call)
            return _check_or_fold(req)
        if tier == "marginal" and pos in _LATE and not facing_raise:
            # Occasional steal
            if rng.random() < 0.3:
                return _raise_to(hand, req, mult=2.5)
            return _check_or_fold(req)
        return _check_or_fold(req)

    # Facing a raise
    if tier == "premium":
        # 3-bet sometimes; call others
        if rng.random() < 0.7:
            return _raise_to(hand, req, mult=3.0, base="opp")
        return Action(ActionType.CALL, req.to_call)
    if tier == "strong":
        # Call most, occasionally 3-bet for value
        if rng.random() < 0.2:
            return _raise_to(hand, req, mult=3.0, base="opp")
        return Action(ActionType.CALL, req.to_call)
    if tier == "decent" and pos in _LATE:
        # Set-mine / float
        return Action(ActionType.CALL, req.to_call) if req.to_call <= player.stack * 0.1 else _check_or_fold(req)
    return _check_or_fold(req)


def _raise_to(hand: Hand, req: ActionRequest, mult: float, base: str = "bb") -> Action:
    bb = hand.table.bb
    if base == "bb":
        target = int(bb * mult)
    else:
        # 3-bet sizing: 3x the opponent's open
        target = int(req.to_call * mult) + req.to_call + req.pot - req.to_call  # roughly
        target = max(target, int(bb * mult * 1.5))
    target = max(target, req.min_raise_to)
    target = min(target, req.max_raise_to)
    return Action(ActionType.RAISE, target)


def _check_or_fold(req: ActionRequest) -> Action:
    if req.legal.can_check:
        return Action(ActionType.CHECK)
    return Action(ActionType.FOLD)


def _maybe_fold_or_call(req: ActionRequest) -> Action:
    if req.legal.can_check:
        return Action(ActionType.CHECK)
    if req.to_call <= 0:
        return Action(ActionType.CHECK)
    return Action(ActionType.FOLD)


# ---------- postflop ----------


def _postflop(hand: Hand, player: Player, req: ActionRequest, rng: random.Random) -> Action:
    # Equity vs random (single opponent assumption — fine for Phase 1).
    eq = equity_vs_random(player.hole_cards, hand.board, n=400, rng=rng)
    facing_bet = req.to_call > 0
    pot = req.pot

    if facing_bet:
        po = pot_odds(req.to_call, pot)
        edge = eq - po
        if eq > 0.78 and req.legal.can_raise:
            # Strong value raise (~2.5x bet)
            target = min(req.min_raise_to * 2, req.max_raise_to)
            return Action(ActionType.RAISE, target)
        if edge > 0.05:
            return Action(ActionType.CALL, req.to_call)
        if edge > -0.03 and rng.random() < 0.3:
            # Bluff catch occasionally
            return Action(ActionType.CALL, req.to_call)
        return Action(ActionType.FOLD)

    # No bet — we can check or bet.
    if eq > 0.65 and req.legal.can_bet:
        # Value bet ~66% pot
        target = max(int(pot * 0.66), hand.table.bb)
        target = max(target, req.min_raise_to if req.legal.can_raise else target)
        target = min(target, req.max_raise_to)
        return Action(ActionType.BET, target) if req.legal.can_bet else Action(ActionType.CHECK)
    if eq > 0.50 and req.legal.can_bet and rng.random() < 0.55:
        # Standard c-bet ~33% pot
        target = max(int(pot * 0.33), hand.table.bb)
        target = min(target, req.max_raise_to)
        return Action(ActionType.BET, target)
    if req.legal.can_check:
        return Action(ActionType.CHECK)
    return Action(ActionType.FOLD)
