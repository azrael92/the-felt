"""Monte Carlo equity calculator.

All functions return win-equity in [0, 1] where ties are split fractionally.
Cards are Treys integers.
"""

from __future__ import annotations

import random
from typing import Iterable

from the_felt.cards import ALL_CARDS
from the_felt.eval import rank_hand


def _remaining_deck(used: Iterable[int]) -> list[int]:
    used_set = set(used)
    return [c for c in ALL_CARDS if c not in used_set]


def equity_vs_hand(
    hero: list[int],
    villain: list[int],
    board: list[int] | None = None,
    n: int = 5000,
    rng: random.Random | None = None,
) -> float:
    """Equity of hero against one known villain hand."""
    board = list(board or [])
    used = [*hero, *villain, *board]
    deck = _remaining_deck(used)
    rng = rng or random.Random()
    cards_needed = 5 - len(board)

    wins = 0.0
    for _ in range(n):
        if cards_needed > 0:
            runout = rng.sample(deck, cards_needed)
        else:
            runout = []
        full_board = board + runout
        h_rank = rank_hand(hero, full_board)
        v_rank = rank_hand(villain, full_board)
        if h_rank < v_rank:
            wins += 1
        elif h_rank == v_rank:
            wins += 0.5
    return wins / n


def equity_vs_random(
    hero: list[int],
    board: list[int] | None = None,
    n: int = 5000,
    rng: random.Random | None = None,
) -> float:
    """Equity of hero against a single uniformly random villain hand."""
    board = list(board or [])
    used = [*hero, *board]
    deck = _remaining_deck(used)
    rng = rng or random.Random()
    cards_needed_for_board = 5 - len(board)

    wins = 0.0
    for _ in range(n):
        # Sample villain's 2 cards + remaining board, all without replacement.
        sample = rng.sample(deck, 2 + cards_needed_for_board)
        villain = sample[:2]
        runout = sample[2:]
        full_board = board + runout
        h_rank = rank_hand(hero, full_board)
        v_rank = rank_hand(villain, full_board)
        if h_rank < v_rank:
            wins += 1
        elif h_rank == v_rank:
            wins += 0.5
    return wins / n


def equity_vs_range(
    hero: list[int],
    villain_combos: list[tuple[int, int]],
    board: list[int] | None = None,
    n: int = 5000,
    rng: random.Random | None = None,
) -> float:
    """Equity of hero against a uniform random sample from a villain range.

    `villain_combos` is a list of 2-tuples of Treys ints. Combos that would
    use cards already on the board or in hero's hand are filtered out.
    """
    board = list(board or [])
    blocked = set([*hero, *board])
    available = [c for c in villain_combos if c[0] not in blocked and c[1] not in blocked]
    if not available:
        # Range is fully blocked — return equity vs random as fallback
        return equity_vs_random(hero, board, n=n, rng=rng)

    rng = rng or random.Random()
    used_base = [*hero, *board]
    base_deck = _remaining_deck(used_base)
    cards_needed_for_board = 5 - len(board)

    wins = 0.0
    for _ in range(n):
        v = available[rng.randrange(len(available))]
        villain = list(v)
        # Remove villain cards from the runout deck
        deck = [c for c in base_deck if c not in villain]
        runout = rng.sample(deck, cards_needed_for_board) if cards_needed_for_board > 0 else []
        full_board = board + runout
        h_rank = rank_hand(hero, full_board)
        v_rank = rank_hand(villain, full_board)
        if h_rank < v_rank:
            wins += 1
        elif h_rank == v_rank:
            wins += 0.5
    return wins / n


def multiway_equity(
    hands: list[list[int]],
    board: list[int] | None = None,
    n: int = 5000,
    rng: random.Random | None = None,
) -> list[float]:
    """Equities for N known hands. Sums to 1.0 (ties split fractionally)."""
    board = list(board or [])
    used = [c for hand in hands for c in hand] + list(board)
    deck = _remaining_deck(used)
    rng = rng or random.Random()
    cards_needed = 5 - len(board)
    k = len(hands)
    scores = [0.0] * k

    for _ in range(n):
        runout = rng.sample(deck, cards_needed) if cards_needed > 0 else []
        full_board = board + runout
        ranks = [rank_hand(h, full_board) for h in hands]
        best = min(ranks)
        winners = [i for i, r in enumerate(ranks) if r == best]
        share = 1.0 / len(winners)
        for i in winners:
            scores[i] += share
    return [s / n for s in scores]
