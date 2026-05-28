"""Preflop hand strength scoring.

We use a precomputed table of equity-vs-random for the 169 starting hand
classes (computed once at import time). The table maps (high_rank, low_rank,
suited) -> equity, where ranks are 0..12 (2..A) and suited is bool.
"""

from __future__ import annotations

import random
from functools import lru_cache

from the_felt.cards import card, rank_of, suit_of
from the_felt.equity.monte_carlo import equity_vs_random


# Build canonical example hands for each of the 169 starting hand classes.
# For pairs: two ranks the same (any 2 suits). For non-pairs: two different
# ranks, either suited or offsuit.

_RANK_CHARS = "23456789TJQKA"


def _canonical_hand(high: int, low: int, suited: bool) -> list[int]:
    """Return a Treys card pair representing this hand class.
    high/low are 0..12, with high >= low (or equal for pairs)."""
    hi_char = _RANK_CHARS[high]
    lo_char = _RANK_CHARS[low]
    if high == low:
        return [card(hi_char + "s"), card(lo_char + "h")]
    if suited:
        return [card(hi_char + "s"), card(lo_char + "s")]
    return [card(hi_char + "s"), card(lo_char + "h")]


# Equity table is populated lazily on first call (avoid 30s import delay).
_EQUITY_TABLE: dict[tuple[int, int, bool], float] = {}


def _populate_table(n: int = 600, seed: int = 12345) -> None:
    """Populate the equity-vs-random table. Call once at startup."""
    rng = random.Random(seed)
    for high in range(13):
        for low in range(high + 1):
            for suited in (False, True):
                if high == low and suited:
                    continue  # pairs can't be "suited"
                hole = _canonical_hand(high, low, suited)
                eq = equity_vs_random(hole, n=n, rng=rng)
                _EQUITY_TABLE[(high, low, suited)] = eq


def _class_of(hole: list[int]) -> tuple[int, int, bool]:
    r1 = rank_of(hole[0])
    r2 = rank_of(hole[1])
    high, low = (r1, r2) if r1 >= r2 else (r2, r1)
    suited = suit_of(hole[0]) == suit_of(hole[1])
    if high == low:
        suited = False
    return (high, low, suited)


def preflop_equity_vs_random(hole: list[int]) -> float:
    """Look up equity-vs-random for a starting hand. Populates table on first call."""
    if not _EQUITY_TABLE:
        _populate_table()
    return _EQUITY_TABLE[_class_of(hole)]


def preflop_strength_tier(hole: list[int]) -> str:
    """Rough categorization useful for rule-based agents."""
    eq = preflop_equity_vs_random(hole)
    if eq >= 0.70:
        return "premium"     # AA-JJ, AK
    if eq >= 0.58:
        return "strong"      # TT-77, AQ-AJ, KQ, suited broadways
    if eq >= 0.50:
        return "decent"      # small pairs, suited connectors, weaker broadways
    if eq >= 0.40:
        return "marginal"    # speculative
    return "trash"


@lru_cache(maxsize=2048)
def _strength_class(high: int, low: int, suited: bool) -> str:
    if not _EQUITY_TABLE:
        _populate_table()
    eq = _EQUITY_TABLE[(high, low, suited)]
    if eq >= 0.70: return "premium"
    if eq >= 0.58: return "strong"
    if eq >= 0.50: return "decent"
    if eq >= 0.40: return "marginal"
    return "trash"


def preflop_strength_tier_fast(hole: list[int]) -> str:
    return _strength_class(*_class_of(hole))


# ---------------------------------------------------------------------------
# Percentile helpers — "is this hand in the top X% of starting hands?"
# ---------------------------------------------------------------------------

# Number of distinct 2-card *combos* for each (high, low, suited) class:
# pairs: 6 combos, suited non-pairs: 4, offsuit non-pairs: 12.
def _class_combos(high: int, low: int, suited: bool) -> int:
    if high == low:
        return 6
    return 4 if suited else 12


_SORTED_CLASSES: list[tuple[float, int, int, bool, int]] | None = None  # (equity, high, low, suited, combos)
_CUM_BELOW: dict[tuple[int, int, bool], int] | None = None  # combos at-or-better when this class is at the threshold


def _build_percentile_index() -> None:
    """Build a sorted list of all 169 hand classes and a cumulative combo count."""
    global _SORTED_CLASSES, _CUM_BELOW
    if not _EQUITY_TABLE:
        _populate_table()
    items = [
        (eq, h, l, s, _class_combos(h, l, s))
        for (h, l, s), eq in _EQUITY_TABLE.items()
    ]
    items.sort(key=lambda x: -x[0])  # best equity first
    _SORTED_CLASSES = items
    _CUM_BELOW = {}
    running = 0
    for eq, h, l, s, combos in items:
        running += combos
        _CUM_BELOW[(h, l, s)] = running
    # 1326 total combos — verify
    assert running == 1326, f"Expected 1326 combos, got {running}"


def hand_percentile(hole: list[int]) -> float:
    """Return percentile rank in [0,1] where 0.0 = best (AA), 1.0 = worst (72o).

    Counts cumulative combos at or stronger than this hand class divided by 1326.
    """
    if _CUM_BELOW is None:
        _build_percentile_index()
    cls = _class_of(hole)
    return _CUM_BELOW[cls] / 1326.0


def in_top_pct(hole: list[int], pct: float) -> bool:
    """True if this hand is in the top `pct` (fraction in [0,1]) of all starting hands.

    Example: in_top_pct(hole, 0.10) is True iff this hand is in the top 10%.
    """
    return hand_percentile(hole) <= max(0.0, min(1.0, pct))


def top_pct_classes(pct: float) -> set[tuple[int, int, bool]]:
    """Return the set of (high, low, suited) class IDs that fall in the top pct."""
    if _SORTED_CLASSES is None:
        _build_percentile_index()
    out: set[tuple[int, int, bool]] = set()
    target_combos = pct * 1326
    running = 0
    for _, h, l, s, combos in _SORTED_CLASSES:
        if running >= target_combos:
            break
        out.add((h, l, s))
        running += combos
    return out
