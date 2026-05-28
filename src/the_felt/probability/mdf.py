"""Minimum Defense Frequency (MDF) and alpha (bluff threshold)."""

from __future__ import annotations


def mdf(pot_before_bet: int, bet: int) -> float:
    """MDF = pot / (pot + bet). The fraction of your range you must defend
    (call or raise) to make your opponent indifferent to bluffing."""
    if bet <= 0:
        return 1.0
    return pot_before_bet / (pot_before_bet + bet)


def alpha(bet: int, pot_before_bet: int) -> float:
    """alpha = bet / (pot + bet). The bluff success rate required to break
    even on a pure bluff of size `bet` into pot."""
    if bet <= 0:
        return 0.0
    return bet / (pot_before_bet + bet)


def bluff_to_value_ratio(bet: int, pot_before_bet: int) -> float:
    """At GTO, on the river, bluffs should be `bet : (pot + bet)` to value.
    Returns the bluff:value ratio as a single float (bluff per 1 value).
    """
    if pot_before_bet + bet <= 0:
        return 0.0
    return bet / (pot_before_bet + bet)
