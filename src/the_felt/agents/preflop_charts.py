"""Preflop opening / 3-bet / call ranges per position and archetype.

We use a percentile-based model rather than hardcoded grids:

- Each position has a baseline "TAG" open percentage.
- Each archetype scales that baseline by `archetype.vpip / TAG.vpip` for VPIP,
  and by `archetype.pfr / archetype.vpip` for the raise vs. limp split within
  voluntary-pot-entry hands.
- A "3-bet" range is the top X% where X is `archetype.three_bet` scaled by
  position aggressiveness.

This gives realistic ranges without hand-crafting 169-cell grids for 7
archetypes × 9 positions × 5 facing-actions. The percentile ranking comes
from `hand_strength.hand_percentile` which is built on actual equity-vs-random.
"""

from __future__ import annotations

from dataclasses import dataclass

from the_felt.agents.archetype import TAG, Archetype
from the_felt.agents.hand_strength import hand_percentile
from the_felt.types import Position


# Baseline TAG opening percentages by position (6-max-ish, 100bb cash).
# Used as the reference. Other archetypes scale relative to this.
POSITION_BASE_OPEN_PCT: dict[Position, float] = {
    Position.UTG: 0.10,
    Position.UTG1: 0.11,
    Position.MP: 0.13,
    Position.LJ: 0.15,
    Position.HJ: 0.18,
    Position.CO: 0.25,
    Position.BTN: 0.42,
    Position.SB: 0.28,
    Position.BB: 0.00,  # BB never opens (would be a check)
}

# Position-relative 3-bet aggressiveness multiplier — late position 3-bets more.
POSITION_3BET_MULT: dict[Position, float] = {
    Position.UTG: 0.6,
    Position.UTG1: 0.7,
    Position.MP: 0.8,
    Position.LJ: 0.9,
    Position.HJ: 1.0,
    Position.CO: 1.1,
    Position.BTN: 1.3,
    Position.SB: 1.4,
    Position.BB: 1.2,
}


@dataclass(frozen=True, slots=True)
class PreflopDecision:
    """Output of preflop chart consultation. One of: raise, call, fold."""

    action: str            # "raise" | "call" | "fold"
    size_bb: float = 0.0   # how many BBs to raise to (only for "raise")


def first_in(arch: Archetype, pos: Position, hole: list[int]) -> PreflopDecision:
    """No one has voluntarily entered the pot yet. Decide raise / limp / fold."""
    base_open = POSITION_BASE_OPEN_PCT.get(pos, 0.0)
    if base_open == 0:
        return PreflopDecision("fold")

    vpip_factor = arch.vpip / max(TAG.vpip, 0.01)
    open_pct = min(1.0, base_open * vpip_factor)

    pct_rank = hand_percentile(hole)
    if pct_rank > open_pct:
        return PreflopDecision("fold")

    # Within the open range, split raise vs limp by PFR/VPIP ratio.
    # E.g. TAG: pfr=0.16 vpip=0.19 → 84% of opens are raises.
    # Calling Station: pfr=0.07 vpip=0.40 → 18% raises, 82% limps.
    raise_share = max(0.05, min(1.0, arch.pfr / max(arch.vpip, 0.01)))

    # Aggressive archetypes raise the top of their range; passive raise sparingly.
    # We deterministically pick "top X% of opens" to raise, rest to call.
    raise_threshold = open_pct * raise_share
    if pct_rank <= raise_threshold:
        # Sizing: nits and tags raise ~2.5x; LAGs and maniacs vary up
        size = 2.5
        if arch.aggression_mult >= 1.5:
            size = 3.0
        if arch.overbet_freq >= 0.15:  # maniac
            size = 3.5
        # Larger opens from late position
        if pos in (Position.BTN, Position.SB):
            size += 0.25
        return PreflopDecision("raise", size_bb=size)

    # Otherwise voluntarily enter for a call (limp).
    return PreflopDecision("call")


def facing_raise(
    arch: Archetype,
    pos: Position,
    hole: list[int],
    raiser_pos: Position | None,
    size_bb: float,
) -> PreflopDecision:
    """Someone raised before us. Decide 3-bet / call / fold."""
    pct_rank = hand_percentile(hole)

    # 3-bet range: archetype.three_bet, scaled by position
    three_bet_pct = arch.three_bet * POSITION_3BET_MULT.get(pos, 1.0)
    three_bet_pct = max(0.005, min(0.30, three_bet_pct))

    if pct_rank <= three_bet_pct:
        # Sizing: 3x the open IP, 4x OOP. Maniacs upsize.
        in_position = pos in (Position.BTN, Position.CO) and raiser_pos != Position.BTN
        base_mult = 3.0 if in_position else 4.0
        if arch.aggression_mult >= 1.5:
            base_mult += 0.5
        if arch.overbet_freq >= 0.15:
            base_mult += 1.0
        return PreflopDecision("raise", size_bb=size_bb * base_mult)

    # Calling range: between 3-bet and a wider continuation threshold.
    # Tight players have a narrow gap between 3bet and fold (mostly 3-bet or fold).
    # Loose-passive players (Calling Station, Whale) have a huge calling range.
    call_top = three_bet_pct
    # Width of the calling range scales with vpip (and inversely with fold-to-3bet
    # because that's a proxy for "willingness to continue against raises").
    call_width = max(0.0, arch.vpip - arch.three_bet) * 0.8
    call_bottom = min(0.70, call_top + call_width)

    # Position adjustments: defend wider in BB.
    if pos == Position.BB:
        call_bottom = min(0.80, call_bottom + 0.15)

    if pct_rank <= call_bottom:
        return PreflopDecision("call")

    return PreflopDecision("fold")


def facing_3bet(
    arch: Archetype,
    pos: Position,
    hole: list[int],
    size_bb: float,
) -> PreflopDecision:
    """We opened, then got 3-bet. Decide 4-bet / call / fold."""
    pct_rank = hand_percentile(hole)

    # 4-bet only with top ~2.5% (KK+, sometimes AKs).
    # Maniacs 4-bet bluff more; nits never 4-bet bluff.
    four_bet_value_pct = 0.025
    four_bet_bluff_extra = 0.0
    if arch.bluff_freq >= 0.20:
        four_bet_bluff_extra = 0.02
    if arch.bluff_freq >= 0.30:
        four_bet_bluff_extra = 0.05
    four_bet_pct = four_bet_value_pct + four_bet_bluff_extra

    if pct_rank <= four_bet_pct:
        size = size_bb * 2.5
        return PreflopDecision("raise", size_bb=size)

    # Calling: middling strong hands (~ 99-TT, AK that didn't 4-bet, suited
    # broadway, suited connectors for set-mining if deep). Width depends
    # on archetype.fold_to_3bet.
    fold_freq = arch.fold_to_3bet
    call_pct = max(0.02, (1.0 - fold_freq) * 0.12)  # tighter the higher fold_to_3bet
    if pct_rank <= four_bet_pct + call_pct:
        return PreflopDecision("call")

    return PreflopDecision("fold")
