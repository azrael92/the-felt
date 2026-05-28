"""Unified archetype-driven decision policy.

`decide(hand, req, archetype, rng) -> Action` picks an action for any seated
bot, using the archetype parameters to modulate ranges, sizing, aggression,
and noise.

Preflop: consults `preflop_charts.first_in / facing_raise / facing_3bet`.
Postflop: hand-strength bucket + texture + archetype aggression knobs.
"""

from __future__ import annotations

import random

from the_felt.agents.archetype import Archetype
from the_felt.agents.preflop_charts import (
    PreflopDecision,
    facing_3bet,
    facing_raise,
    first_in,
)
from the_felt.agents.texture import analyze as analyze_texture
from the_felt.engine.hand import ActionRequest, Hand
from the_felt.engine.table import Player
from the_felt.equity.monte_carlo import equity_vs_random
from the_felt.probability.pot_odds import pot_odds
from the_felt.types import Action, ActionType, Street


def decide(
    hand: Hand,
    req: ActionRequest,
    archetype: Archetype,
    rng: random.Random | None = None,
) -> Action:
    rng = rng or random.Random()
    player = hand.table.players[req.seat]
    if req.street == Street.PREFLOP:
        return _decide_preflop(hand, player, req, archetype, rng)
    return _decide_postflop(hand, player, req, archetype, rng)


# ---------------------------------------------------------------------------
# Preflop
# ---------------------------------------------------------------------------

def _decide_preflop(
    hand: Hand,
    player: Player,
    req: ActionRequest,
    arch: Archetype,
    rng: random.Random,
) -> Action:
    bb = hand.table.bb

    # Classify the situation: first_in, facing_raise, or facing_3bet
    # Find any raises this street from the hand history
    raises = _preflop_raises(hand)
    my_raise = _my_last_raise_size_bb(hand, player.id, bb)
    last_raiser_seat: int | None = None
    last_raise_size_bb = 0.0
    for ev in raises:
        if ev.data["player_id"] != player.id:
            last_raiser_seat = ev.data["seat"]
            last_raise_size_bb = ev.data["amount"] / bb

    facing_bet = req.to_call > 0
    facing_raise_above_bb = req.to_call > 0 and (
        req.to_call > bb
        or (player.position and player.position.value == "BB" and req.to_call > 0 and any(
            ev.data["action"] == "raise" for ev in raises
        ))
    )

    # Track whether we already raised this street (= we are the original raiser)
    we_raised = my_raise > 0
    # Count distinct raises by other players this hand to detect 3-bets vs 4-bets etc.
    other_raises_count = sum(1 for ev in raises if ev.data["player_id"] != player.id)

    if we_raised and other_raises_count >= 1:
        # We opened, got 3-bet → 4-bet/call/fold
        decision = facing_3bet(arch, player.position, player.hole_cards, last_raise_size_bb)
    elif facing_raise_above_bb:
        decision = facing_raise(
            arch, player.position, player.hole_cards,
            raiser_pos=hand.table.players[last_raiser_seat].position if last_raiser_seat is not None else None,
            size_bb=last_raise_size_bb,
        )
    else:
        # First in (or limp pot)
        decision = first_in(arch, player.position, player.hole_cards)

    # Add archetype noise: occasionally swap action.
    decision = _jitter(decision, arch, rng)

    return _materialize(hand, req, decision)


def _materialize(hand: Hand, req: ActionRequest, decision: PreflopDecision) -> Action:
    bb = hand.table.bb
    legal = req.legal

    if decision.action == "fold":
        # Free pass if no bet to call
        if legal.can_check:
            return Action(ActionType.CHECK)
        return Action(ActionType.FOLD)

    if decision.action == "call":
        if legal.can_check:
            return Action(ActionType.CHECK)
        if legal.can_call:
            # Cap at 25% of stack pre — avoid silly calls of all-in shoves with junk
            return Action(ActionType.CALL, req.to_call)
        return Action(ActionType.FOLD)

    if decision.action == "raise":
        target = max(int(decision.size_bb * bb), legal.min_raise_to)
        target = min(target, legal.max_raise_to)
        if legal.can_bet:
            return Action(ActionType.BET, target)
        if legal.can_raise:
            return Action(ActionType.RAISE, target)
        # Can't actually raise → call or check fallback
        if legal.can_check:
            return Action(ActionType.CHECK)
        if legal.can_call:
            return Action(ActionType.CALL, req.to_call)
        return Action(ActionType.FOLD)

    return Action(ActionType.FOLD)


def _jitter(decision: PreflopDecision, arch: Archetype, rng: random.Random) -> PreflopDecision:
    """Occasional action swap based on archetype noise."""
    if rng.random() > arch.noise_sigma:
        return decision
    # With probability noise_sigma, do something slightly different.
    if decision.action == "fold":
        # Sometimes call instead — limp/loose pass
        return PreflopDecision("call")
    if decision.action == "call":
        # Sometimes mini-raise instead (loose), sometimes fold (tight)
        if arch.aggression_mult >= 1.0 and rng.random() < 0.5:
            return PreflopDecision("raise", size_bb=2.5)
        return PreflopDecision("fold")
    if decision.action == "raise":
        # Sometimes just call (tight) or size differently
        if arch.aggression_mult < 1.0 and rng.random() < 0.5:
            return PreflopDecision("call")
        return PreflopDecision("raise", size_bb=decision.size_bb * (1.0 + (rng.random() - 0.5) * 0.4))
    return decision


def _preflop_raises(hand: Hand):
    out = []
    for ev in hand.history.events:
        if ev.kind != "player_action":
            continue
        if ev.data.get("street") != "preflop":
            continue
        if ev.data.get("action") in ("raise", "bet"):
            out.append(ev)
    return out


def _my_last_raise_size_bb(hand: Hand, player_id: str, bb: int) -> float:
    last = 0.0
    for ev in hand.history.events:
        if ev.kind != "player_action":
            continue
        if ev.data.get("street") != "preflop":
            continue
        if ev.data.get("player_id") != player_id:
            continue
        if ev.data.get("action") in ("raise", "bet"):
            last = ev.data["amount"] / bb
    return last


# ---------------------------------------------------------------------------
# Postflop
# ---------------------------------------------------------------------------

def _decide_postflop(
    hand: Hand,
    player: Player,
    req: ActionRequest,
    arch: Archetype,
    rng: random.Random,
) -> Action:
    legal = req.legal
    pot = req.pot
    bb = hand.table.bb

    # Equity vs random — Phase 2 will refine to archetype-aware villain ranges,
    # but the bot uses equity_vs_random for now (the coach will use range-aware).
    eq = equity_vs_random(player.hole_cards, hand.board, n=350, rng=rng)
    texture = analyze_texture(hand.board)

    # Adjust equity perception slightly by archetype:
    # - Calling stations don't think about texture; they call with bottom pair forever
    # - Maniacs perceive their equity as higher than it is (overconfident)
    perceived = eq + (arch.aggression_mult - 1.0) * 0.05
    perceived = max(0.0, min(1.0, perceived))

    facing_bet = req.to_call > 0

    # Apply pre-flop aggressor c-bet bias: if no bet yet and we were the PFR,
    # increase our c-bet frequency by archetype cbet_freq.
    was_pfr = _was_preflop_aggressor(hand, player.id)

    if facing_bet:
        po = pot_odds(req.to_call, pot)
        edge = perceived - po

        # Strong hand → raise for value
        value_threshold = 0.78 - 0.05 * (arch.aggression_mult - 1.0)
        if perceived >= value_threshold and legal.can_raise:
            size = _size_raise(arch, pot, req, rng)
            return Action(ActionType.RAISE, size)

        # Edge positive enough → call
        edge_required = 0.05 - (arch.tightness_mult - 1.0) * 0.04
        if edge > edge_required:
            return Action(ActionType.CALL, req.to_call)

        # Bluff-catch zone — calling station effect
        if edge > -0.05 and arch.tightness_mult <= 0.7:
            return Action(ActionType.CALL, req.to_call)

        # Maniacs occasionally bluff-raise even with weak hands
        if arch.bluff_freq > 0.20 and rng.random() < arch.bluff_freq * 0.5 and legal.can_raise:
            size = _size_raise(arch, pot, req, rng)
            return Action(ActionType.RAISE, size)

        return Action(ActionType.FOLD)

    # No bet — we can check or bet.
    # Value bet with strong hands
    if perceived >= 0.65 and legal.can_bet:
        size = _size_bet(arch, pot, req, rng, kind="value", texture=texture)
        return Action(ActionType.BET, size)

    # C-bet (whether we were PFR or just have a piece) — frequency per archetype
    cbet_chance = arch.cbet_freq
    if was_pfr:
        cbet_chance *= 1.0
    else:
        cbet_chance *= 0.4  # only "donk" if not PFR — much less common
    # Texture: dry boards encourage smaller c-bets across all archetypes
    if texture.nut_advantage_pfa > 0.3 and was_pfr:
        cbet_chance += 0.15

    if perceived >= 0.40 and legal.can_bet and rng.random() < cbet_chance:
        size = _size_bet(arch, pot, req, rng, kind="cbet", texture=texture)
        return Action(ActionType.BET, size)

    # Pure bluff with air — only aggressive archetypes
    if perceived < 0.30 and legal.can_bet and rng.random() < arch.bluff_freq:
        size = _size_bet(arch, pot, req, rng, kind="bluff", texture=texture)
        return Action(ActionType.BET, size)

    if legal.can_check:
        return Action(ActionType.CHECK)
    return Action(ActionType.FOLD)


def _size_bet(arch: Archetype, pot: int, req: ActionRequest, rng: random.Random, kind: str, texture) -> int:
    """Pick a bet size based on archetype style + kind (value/cbet/bluff)."""
    # Base fraction of pot per kind:
    if kind == "value":
        frac = 0.66
    elif kind == "cbet":
        frac = 0.33 if texture.nut_advantage_pfa > 0.2 else 0.50
    else:  # bluff
        frac = 0.50
    # Aggressive archetypes upsize; passive downsize
    frac *= (0.7 + arch.aggression_mult * 0.3)
    # Maniacs overbet
    if arch.overbet_freq > 0.10 and rng.random() < arch.overbet_freq:
        frac = max(frac, 1.3)
    target = int(max(req.legal.min_raise_to if req.legal.can_raise else 0, pot * frac))
    target = max(target, req.legal.min_raise_to if req.legal.can_raise else int(pot * frac))
    target = min(target, req.legal.max_raise_to)
    return target


def _size_raise(arch: Archetype, pot: int, req: ActionRequest, rng: random.Random) -> int:
    """Raise sizing — typically 2.5-3x the facing bet, scaled by archetype."""
    base_mult = 2.5 + (arch.aggression_mult - 1.0) * 0.5
    if arch.overbet_freq > 0.10 and rng.random() < arch.overbet_freq:
        base_mult += 1.5
    target = int(req.to_call * base_mult) + req.legal.min_raise_to
    target = max(target, req.legal.min_raise_to)
    target = min(target, req.legal.max_raise_to)
    return target


def _was_preflop_aggressor(hand: Hand, player_id: str) -> bool:
    last_pfr = None
    for ev in hand.history.events:
        if ev.kind == "player_action" and ev.data.get("street") == "preflop":
            if ev.data["action"] in ("raise", "bet"):
                last_pfr = ev.data["player_id"]
    return last_pfr == player_id
