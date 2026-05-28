"""Side pot calculation.

Given each player's total chip contribution to the hand and a set of
still-eligible (non-folded) player ids, compute the list of pots in order
(main pot first, then progressively smaller side pots).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Pot:
    amount: int
    eligible: tuple[str, ...]  # player ids that can win this pot


def build_pots(
    contributions: dict[str, int],
    eligible: set[str],
) -> list[Pot]:
    """Build side pots from total per-player contributions.

    Folded players still contribute money to pots, but are not eligible to
    win them. Pass `eligible` containing only non-folded players.

    Returns a list of pots in main->side order. Empty pots are omitted.
    """
    if not contributions:
        return []

    # Each pot is bounded by the next-larger all-in tier from eligible
    # players. Contributions from folded players are "dead money" that
    # gets folded into the pot at their contribution level.
    pots: list[Pot] = []
    # Get distinct contribution levels among eligible players, ascending.
    eligible_levels = sorted({contributions[p] for p in eligible if p in contributions})
    if not eligible_levels:
        # No eligible players → everything is dead money but cannot be
        # awarded. Caller should handle this edge case.
        return []

    prev = 0
    for level in eligible_levels:
        slice_size = level - prev
        if slice_size <= 0:
            continue
        # Every player (eligible or folded) who contributed at least
        # `prev + 1` pays into this slice up to `slice_size`.
        amount = 0
        pot_eligible: list[str] = []
        for pid, total in contributions.items():
            paid_into_slice = max(0, min(total, level) - prev)
            amount += paid_into_slice
            if pid in eligible and total >= level:
                pot_eligible.append(pid)
        if amount > 0 and pot_eligible:
            pots.append(Pot(amount=amount, eligible=tuple(pot_eligible)))
        prev = level

    # Excess contributions above the highest eligible level go back to
    # whoever contributed them (uncalled bet).
    return pots


def uncalled_refund(
    contributions: dict[str, int],
    eligible: set[str],
) -> tuple[str, int] | None:
    """If a player committed more than anyone else who could call, refund the diff."""
    if not contributions or not eligible:
        return None
    # Find the highest level among contributors who CAN call (eligible OR
    # all-in-for-less folded — but folded players don't get refunds).
    callable_levels = sorted(contributions.values(), reverse=True)
    if len(callable_levels) < 2:
        # Only one contributor — they get everything back
        only = next(iter(contributions))
        return (only, contributions[only])
    top = callable_levels[0]
    second = callable_levels[1]
    if top > second:
        # The single highest contributor gets refunded the excess
        winner_pid = max(contributions, key=lambda p: contributions[p])
        return (winner_pid, top - second)
    return None
