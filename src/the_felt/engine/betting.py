"""Betting round mechanics.

The convention used throughout: `Action.amount` for BET/RAISE/CALL/ALL_IN is
the TOTAL chips that the acting player will have committed to the CURRENT
street after the action (the "raise-to" convention). For CHECK and FOLD,
amount is ignored.

This makes legality checking and pot accounting clean — chips moved into
the pot by an action are always `action.amount - player.committed_street`.
"""

from __future__ import annotations

from dataclasses import dataclass

from the_felt.engine.table import Player, Table
from the_felt.types import Action, ActionType, LegalActions


@dataclass(slots=True)
class BettingState:
    """Live state of a betting round for one street."""

    current_bet: int = 0           # highest committed_street among live players
    last_raise_size: int = 0       # last *increment* (used for min-raise rule)
    last_aggressor_seat: int = -1  # seat that last bet/raised
    current_actor_seat: int = -1   # whose turn it is
    closed: bool = False           # round is over

    def reset_for_street(self) -> None:
        self.current_bet = 0
        self.last_raise_size = 0
        self.last_aggressor_seat = -1
        self.current_actor_seat = -1
        self.closed = False


def legal_actions_for(player: Player, state: BettingState, bb: int) -> LegalActions:
    """Compute what `player` may legally do right now."""
    to_call = state.current_bet - player.committed_street
    can_check = to_call == 0
    can_call = to_call > 0 and player.stack > 0
    can_fold = to_call > 0  # only fold when facing a bet (else it's just a check)

    # Bet: only when no one has bet this street (current_bet == 0)
    can_bet = state.current_bet == 0 and player.stack > 0
    # Raise: only when facing a bet
    can_raise = state.current_bet > 0 and player.stack > to_call

    # Min raise amount: previous raise size, floored at bb.
    min_raise_increment = max(state.last_raise_size, bb)
    min_raise_to = state.current_bet + min_raise_increment
    # All-in cap:
    max_to = player.committed_street + player.stack

    return LegalActions(
        can_fold=can_fold,
        can_check=can_check,
        can_call=can_call,
        call_amount=max(to_call, 0),
        can_bet=can_bet,
        can_raise=can_raise,
        min_raise_to=min(min_raise_to, max_to),
        max_raise_to=max_to,
    )


def apply_action(
    table: Table,
    state: BettingState,
    actor: Player,
    action: Action,
    bb: int,
) -> None:
    """Apply `action` from `actor`. Mutates actor and state.

    Validation is centralized here — caller should still check legal_actions_for
    before offering choices to a UI, but illegal inputs raise ValueError here.
    """
    legal = legal_actions_for(actor, state, bb)

    if action.type == ActionType.FOLD:
        if not legal.can_fold:
            raise ValueError("Cannot fold — no bet to face")
        actor.is_folded = True
        actor.has_acted_this_street = True
        return

    if action.type == ActionType.CHECK:
        if not legal.can_check:
            raise ValueError("Cannot check — there is a bet to call")
        actor.has_acted_this_street = True
        return

    if action.type == ActionType.CALL:
        if not legal.can_call:
            raise ValueError("Cannot call")
        pay = min(legal.call_amount, actor.stack)
        actor.stack -= pay
        actor.committed_street += pay
        actor.committed_total += pay
        if actor.stack == 0:
            actor.is_all_in = True
        actor.has_acted_this_street = True
        return

    if action.type == ActionType.BET:
        if not legal.can_bet:
            raise ValueError("Cannot bet — already a bet to call")
        bet_to = action.amount
        if bet_to > legal.max_raise_to:
            raise ValueError(f"Bet too large: {bet_to} > {legal.max_raise_to}")
        # Bet must be at least one BB (or all-in)
        is_all_in = bet_to == legal.max_raise_to
        if not is_all_in and bet_to < bb:
            raise ValueError(f"Bet must be at least one BB ({bb})")
        delta = bet_to - actor.committed_street
        actor.stack -= delta
        actor.committed_street = bet_to
        actor.committed_total += delta
        if actor.stack == 0:
            actor.is_all_in = True
        state.current_bet = bet_to
        state.last_raise_size = bet_to
        state.last_aggressor_seat = actor.seat
        # Reset has_acted for everyone else still live
        _reopen_action(table, except_seat=actor.seat)
        actor.has_acted_this_street = True
        return

    if action.type == ActionType.RAISE:
        if not legal.can_raise:
            raise ValueError("Cannot raise")
        raise_to = action.amount
        if raise_to > legal.max_raise_to:
            raise ValueError(f"Raise-to too large: {raise_to} > {legal.max_raise_to}")
        is_all_in = raise_to == legal.max_raise_to
        # Raise must meet min-raise unless it's an all-in for less.
        if not is_all_in and raise_to < legal.min_raise_to:
            raise ValueError(
                f"Raise-to {raise_to} below min-raise {legal.min_raise_to}"
            )
        raise_increment = raise_to - state.current_bet
        delta = raise_to - actor.committed_street
        actor.stack -= delta
        actor.committed_street = raise_to
        actor.committed_total += delta
        if actor.stack == 0:
            actor.is_all_in = True
        state.current_bet = raise_to
        # Only "full" raises reopen action and update last_raise_size.
        if raise_increment >= state.last_raise_size:
            state.last_raise_size = raise_increment
            state.last_aggressor_seat = actor.seat
            _reopen_action(table, except_seat=actor.seat)
        actor.has_acted_this_street = True
        return

    if action.type == ActionType.ALL_IN:
        # Equivalent to raise-to (or bet-to) at max_raise_to
        all_in_to = legal.max_raise_to
        if state.current_bet == 0:
            substitute = Action(type=ActionType.BET, amount=all_in_to)
        else:
            substitute = Action(type=ActionType.RAISE, amount=all_in_to)
        # Edge case: all-in is below min-raise but above current_bet — treat as a partial raise.
        # The legality check inside BET/RAISE above will accept all_in_to == max_raise_to.
        apply_action(table, state, actor, substitute, bb)
        return

    raise ValueError(f"Unknown action: {action}")


def _reopen_action(table: Table, except_seat: int) -> None:
    """Reset has_acted_this_street for all live players except the aggressor."""
    for p in table.players:
        if p.seat == except_seat:
            continue
        if p.is_folded or p.is_all_in:
            continue
        p.has_acted_this_street = False


def round_is_closed(table: Table, state: BettingState) -> bool:
    """True if no live player still owes action this street."""
    live = [p for p in table.players if not p.is_folded]
    # Only one player left → round is closed (and the hand ends elsewhere).
    if len(live) <= 1:
        return True
    # Everyone but one is all-in → no more betting possible.
    can_still_act = [p for p in live if not p.is_all_in]
    if len(can_still_act) <= 1:
        # The single remaining caller can still act if they haven't matched the bet.
        if len(can_still_act) == 1:
            p = can_still_act[0]
            if not p.has_acted_this_street and p.committed_street < state.current_bet:
                return False
        return True
    # Standard: everyone has acted and matched the current bet (or is all-in).
    for p in can_still_act:
        if not p.has_acted_this_street:
            return False
        if p.committed_street < state.current_bet:
            return False
    return True


def next_actor_seat(table: Table, state: BettingState) -> int | None:
    """Find next seat to act after current_actor_seat. None if round is closed."""
    if round_is_closed(table, state):
        return None
    n = table.num_players()
    seat = state.current_actor_seat
    for _ in range(n):
        seat = (seat + 1) % n
        p = table.players[seat]
        if p.is_folded or p.is_all_in:
            continue
        if p.has_acted_this_street and p.committed_street >= state.current_bet:
            continue
        return seat
    return None
