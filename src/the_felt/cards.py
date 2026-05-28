"""Card and Deck wrappers over Treys."""

from __future__ import annotations

import random
from typing import Iterable

from treys import Card as TreysCard
from treys import Deck as TreysDeck


# A Card is just the Treys int. We expose a thin namespace of helpers.

RANKS = "23456789TJQKA"
SUITS = "shdc"  # spades, hearts, diamonds, clubs

ALL_CARDS_STR: list[str] = [r + s for r in RANKS for s in SUITS]
ALL_CARDS: list[int] = [TreysCard.new(c) for c in ALL_CARDS_STR]


def card(s: str) -> int:
    """Parse a card string like 'Ah' or 'Td' into a Treys int."""
    return TreysCard.new(s)


def to_str(c: int) -> str:
    """Convert a Treys int back to a 2-char string like 'Ah'."""
    return TreysCard.int_to_str(c)


def pretty(c: int) -> str:
    """Unicode pretty-print, e.g. ' A♥ '."""
    return TreysCard.int_to_pretty_str(c)


def rank_of(c: int) -> int:
    """Numeric rank 0..12 (2..A)."""
    return TreysCard.get_rank_int(c)


def suit_of(c: int) -> int:
    """Treys-encoded suit bit (1, 2, 4, or 8)."""
    return TreysCard.get_suit_int(c)


class Deck:
    """A standard 52-card deck. Mutable; cards removed after deal()."""

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._cards: list[int] = list(ALL_CARDS)
        self._rng.shuffle(self._cards)

    def deal(self, n: int = 1) -> list[int]:
        if n > len(self._cards):
            raise RuntimeError("Deck exhausted")
        out = self._cards[:n]
        self._cards = self._cards[n:]
        return out

    def remove(self, cards: Iterable[int]) -> None:
        for c in cards:
            if c in self._cards:
                self._cards.remove(c)

    def remaining(self) -> int:
        return len(self._cards)
