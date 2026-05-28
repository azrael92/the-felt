"""Pot odds and required equity."""

from __future__ import annotations


def pot_odds(to_call: int, pot: int) -> float:
    """Required equity to break even on a call.

    `pot` is the size of the pot BEFORE the call.
    `to_call` is the chips needed to call (the size of the bet facing the player).
    Returns 0.0 if to_call is 0.
    """
    if to_call <= 0:
        return 0.0
    return to_call / (pot + to_call)


def required_equity(to_call: int, pot: int) -> float:
    """Alias for pot_odds — read more naturally in some commentary."""
    return pot_odds(to_call, pot)


def odds_ratio(to_call: int, pot: int) -> str:
    """Format as 'X:1' ratio for traditional poker-speak."""
    if to_call <= 0:
        return "free"
    ratio = pot / to_call
    return f"{ratio:.1f}:1"
