"""Outs counting and the rule of 2 & 4."""

from __future__ import annotations

from the_felt.cards import ALL_CARDS
from the_felt.eval import rank_class, rank_hand


def rule_of_2_and_4(outs: int, street: str) -> tuple[float, float]:
    """Return (probability to improve on next card, by river).

    On the flop with one card to come: outs * 2 %.
    On the flop assuming we see both turn and river (all-in): outs * 4 %.
    On the turn: outs * 2 %.

    Returns the proportion (0..1), not percentage.
    """
    if street == "flop":
        next_card = min(outs / 47.0, 1.0)
        by_river = 1 - ((47 - outs) / 47) * ((46 - outs) / 46)
        return (next_card, by_river)
    if street == "turn":
        return (min(outs / 46.0, 1.0), min(outs / 46.0, 1.0))
    return (0.0, 0.0)


def outs_breakdown(hero: list[int], board: list[int]) -> dict:
    """Categorized breakdown of every out, grouped by the resulting hand class.

    Returns a dict shaped like:
        {
          "total": 12,
          "categories": [
            {"name": "Flush", "count": 9, "cards": ["As", "Ks", ...]},
            {"name": "Straight", "count": 3, "cards": ["6h", "6d", "6c"]},
          ],
          "current_class_name": "High card",
        }

    This is what the teacher uses to show "you said 8, but here are the
    9 flush outs + 3 straight outs = 12" instead of just "wrong, it's 12".
    """
    from the_felt.cards import to_str

    if len(board) < 3:
        return {"total": 0, "categories": [], "current_class_name": ""}

    current_rank = rank_hand(hero, board)
    current_class = rank_class(current_rank)
    used = set(hero + board)
    remaining = [c for c in ALL_CARDS if c not in used]

    # Bucket each improving card by the hand class it produces
    buckets: dict[int, list[int]] = {}
    for c in remaining:
        new_rank = rank_hand(hero, board + [c])
        new_class = rank_class(new_rank)
        if new_class < current_class:    # strictly-better class
            buckets.setdefault(new_class, []).append(c)

    # Treys class index → human name
    CLASS_NAMES = {
        0: "Royal Flush",
        1: "Straight Flush",
        2: "Four of a Kind",
        3: "Full House",
        4: "Flush",
        5: "Straight",
        6: "Three of a Kind",
        7: "Two Pair",
        8: "Pair",
        9: "High Card",
    }

    # Sort categories best-class first (lowest index)
    categories = []
    for class_idx in sorted(buckets.keys()):
        cards = buckets[class_idx]
        categories.append({
            "name": CLASS_NAMES.get(class_idx, f"class {class_idx}"),
            "count": len(cards),
            "cards": [to_str(c) for c in cards],
        })

    return {
        "total": sum(len(v) for v in buckets.values()),
        "categories": categories,
        "current_class_name": CLASS_NAMES.get(current_class, "?"),
    }


def count_outs(hero: list[int], board: list[int], threshold_rank: int | None = None) -> int:
    """Count the number of remaining cards that *meaningfully* improve hero's hand.

    "Meaningful" = the new card produces a strictly better HAND CLASS
    (e.g. high-card → pair, pair → two-pair, straight-draw → straight).
    Pure rank-within-class improvements (kicker upgrades) are excluded
    because they rarely matter in actual play.

    With `threshold_rank` set, we count outs that beat that target rank
    instead (useful for "outs to beat villain's likely hand").
    """
    if len(board) < 3:
        return 0
    current_rank = rank_hand(hero, board) if len(hero) + len(board) >= 5 else None
    if current_rank is None:
        return 0
    current_class = rank_class(current_rank)
    target_class = (
        rank_class(threshold_rank) if threshold_rank is not None else current_class
    )

    used = set(hero + board)
    remaining = [c for c in ALL_CARDS if c not in used]
    outs = 0
    for c in remaining:
        new_board = board + [c]
        new_rank = rank_hand(hero, new_board)
        new_class = rank_class(new_rank)
        # Strictly-better class (lower index = better in Treys).
        if new_class < target_class:
            outs += 1
    return outs
