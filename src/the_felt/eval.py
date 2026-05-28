"""Hand evaluation via Treys."""

from __future__ import annotations

from functools import lru_cache

from treys import Evaluator


_EVAL = Evaluator()


def rank_hand(hole: list[int], board: list[int]) -> int:
    """Treys rank: lower is better. 1 = royal flush, 7462 = worst high card.

    Requires 2 hole cards and 0/3/4/5 board cards. With fewer than 5 total
    cards, returns -1 (cannot rank yet).
    """
    total = len(hole) + len(board)
    if total < 5:
        return -1
    return _EVAL.evaluate(board, hole)


def rank_class(rank: int) -> int:
    """1..9 hand class (1=straight flush, 9=high card)."""
    return _EVAL.get_rank_class(rank)


@lru_cache(maxsize=10)
def class_to_string(cls: int) -> str:
    return _EVAL.class_to_string(cls)


def describe(hole: list[int], board: list[int]) -> str:
    r = rank_hand(hole, board)
    if r < 0:
        return "incomplete"
    return class_to_string(rank_class(r))


def compare(a_rank: int, b_rank: int) -> int:
    """Returns -1 if a beats b, 1 if b beats a, 0 if tie (Treys: lower is better)."""
    if a_rank < b_rank:
        return -1
    if a_rank > b_rank:
        return 1
    return 0
