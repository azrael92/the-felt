"""Board texture analysis.

Outputs a `Texture` describing flop/turn/river-relevant features:
  - wet vs dry (lots of draws vs few)
  - paired
  - monotone vs flush-draw vs rainbow
  - connectivity (straight draws)
  - high-card vs low-card
  - nut_advantage_pfa: rough -1..+1 score of how board favors the preflop aggressor
"""

from __future__ import annotations

from dataclasses import dataclass

from the_felt.cards import rank_of, suit_of


@dataclass(frozen=True, slots=True)
class Texture:
    wet: bool
    paired: bool
    monotone: bool          # all same suit
    two_tone: bool          # 2 of one suit (flush draw possible)
    connected: bool         # 2 or more cards within 2 ranks
    high_card_present: bool  # T or higher
    paired_top: bool        # the pair includes the highest card on board
    nut_advantage_pfa: float  # -1..+1 (positive favors preflop aggressor)


def _rank_set(cards: list[int]) -> list[int]:
    return sorted([rank_of(c) for c in cards])


def _suit_counts(cards: list[int]) -> dict[int, int]:
    out: dict[int, int] = {}
    for c in cards:
        s = suit_of(c)
        out[s] = out.get(s, 0) + 1
    return out


def analyze(board: list[int]) -> Texture:
    """Analyze a 3, 4, or 5-card board."""
    if len(board) < 3:
        return Texture(False, False, False, False, False, False, False, 0.0)

    ranks = _rank_set(board)
    rank_counts: dict[int, int] = {}
    for r in ranks:
        rank_counts[r] = rank_counts.get(r, 0) + 1
    paired = any(v >= 2 for v in rank_counts.values())
    paired_top = paired and max(rank_counts, key=lambda k: (rank_counts[k], k)) == max(ranks)

    suits = _suit_counts(board)
    monotone = max(suits.values()) >= 3 and len(suits) == 1
    two_tone = max(suits.values()) == 2

    # Connectivity: max gap between any two distinct ranks
    distinct_ranks = sorted(set(ranks))
    connected = False
    if len(distinct_ranks) >= 2:
        for i in range(len(distinct_ranks) - 1):
            if distinct_ranks[i + 1] - distinct_ranks[i] <= 2:
                connected = True
                break

    high_card_present = max(ranks) >= 8  # T or higher (0-indexed: T=8, J=9, Q=10, K=11, A=12)

    wet = (connected and not paired) or two_tone or monotone

    # Nut advantage heuristic: high-card, non-paired, non-monotone boards
    # favor the preflop aggressor (they have more AA/KK/AK in range).
    # Low-paired, low-connected boards favor the caller.
    nut_advantage = 0.0
    if high_card_present:
        nut_advantage += 0.4
    if max(ranks) >= 10:  # Q or higher
        nut_advantage += 0.2
    if paired and max(ranks) < 8:
        nut_advantage -= 0.3  # low paired boards bad for PFA
    if connected and max(ranks) < 8:
        nut_advantage -= 0.2
    if monotone:
        nut_advantage -= 0.15
    nut_advantage = max(-1.0, min(1.0, nut_advantage))

    return Texture(
        wet=wet,
        paired=paired,
        monotone=monotone,
        two_tone=two_tone,
        connected=connected,
        high_card_present=high_card_present,
        paired_top=paired_top,
        nut_advantage_pfa=nut_advantage,
    )
