"""Estimate the range of an opponent given their archetype and actions this hand.

This is a simplified Bayesian-flavored estimator: we start with the archetype's
default range for whatever action they took, and shrink it as more bets land
on later streets. It's good enough for the coach to compute "equity vs.
estimated range" — much more meaningful than equity vs. random.

Returned ranges are lists of (card_a, card_b) Treys-int tuples that the
equity calculator's `equity_vs_range` can sample uniformly.
"""

from __future__ import annotations

from the_felt.agents.archetype import Archetype
from the_felt.agents.hand_strength import top_pct_classes
from the_felt.cards import ALL_CARDS, suit_of, rank_of
from the_felt.engine.hand import Hand
from the_felt.engine.table import Player
from the_felt.types import ActionType


# How much each subsequent betting action shrinks the range (multiplicative on pct).
STREET_SHRINK = {
    "preflop": 1.0,
    "flop": 0.55,
    "turn": 0.40,
    "river": 0.30,
}


def estimate_range(
    hand: Hand,
    villain: Player,
    arch: Archetype,
    blocked_cards: list[int],
) -> list[tuple[int, int]]:
    """Build the villain's combo list given their archetype and the actions
    they've taken this hand.

    `blocked_cards` are cards we know aren't in their hand (hero cards + board).
    """
    classes = _start_range_classes(hand, villain, arch)
    # Shrink by latest street activity
    last_street = _last_street_with_action(hand, villain)
    shrink = STREET_SHRINK.get(last_street, 1.0)
    if shrink < 1.0:
        # Take the strongest fraction (sorted top-pct first)
        # We approximate by using top_pct_classes with a smaller pct.
        # Convert our current set's effective % then shrink.
        # Simplest: replace with top (current_pct * shrink) of all hands.
        effective_pct = len(classes) / 169.0 * shrink
        classes = top_pct_classes(min(0.95, max(0.005, effective_pct)))

    return _classes_to_combos(classes, blocked_cards)


def _start_range_classes(hand: Hand, villain: Player, arch: Archetype) -> set[tuple[int, int, bool]]:
    """Decide the villain's initial range based on what they did preflop."""
    # Count their preflop actions
    pf_actions = [
        ev for ev in hand.history.events
        if ev.kind == "player_action"
        and ev.data.get("player_id") == villain.id
        and ev.data.get("street") == "preflop"
    ]
    raised = any(ev.data.get("action") in ("raise", "bet") for ev in pf_actions)
    called = any(ev.data.get("action") == "call" for ev in pf_actions)

    if raised:
        # Their open/3bet/4bet range — use vpip & three_bet
        pct = min(0.30, max(0.02, arch.vpip if not _was_3bet(hand, villain) else arch.three_bet))
    elif called:
        # Calling range = vpip - pfr
        pct = max(0.05, min(0.50, arch.vpip - arch.pfr * 0.5))
    else:
        # Limp / cold-call / unknown — use vpip
        pct = arch.vpip

    return top_pct_classes(pct)


def _was_3bet(hand: Hand, villain: Player) -> bool:
    pf_raises = [
        ev for ev in hand.history.events
        if ev.kind == "player_action"
        and ev.data.get("street") == "preflop"
        and ev.data.get("action") in ("raise", "bet")
    ]
    if not pf_raises:
        return False
    # The villain's first raise was after at least one other raise this street
    villain_first_raise_idx = next(
        (i for i, ev in enumerate(pf_raises) if ev.data["player_id"] == villain.id),
        None,
    )
    return villain_first_raise_idx is not None and villain_first_raise_idx > 0


def _last_street_with_action(hand: Hand, villain: Player) -> str:
    last = "preflop"
    for ev in hand.history.events:
        if ev.kind == "player_action" and ev.data.get("player_id") == villain.id:
            last = ev.data.get("street", "preflop")
    return last


def _classes_to_combos(
    classes: set[tuple[int, int, bool]],
    blocked: list[int],
) -> list[tuple[int, int]]:
    """Expand a set of (high, low, suited) hand classes to all individual
    2-card combos (Treys ints), filtering out combos that share a card with
    any `blocked` card."""
    blocked_set = set(blocked)
    out: list[tuple[int, int]] = []
    # Index cards by rank for quick lookup
    by_rank: dict[int, list[int]] = {}
    for c in ALL_CARDS:
        by_rank.setdefault(rank_of(c), []).append(c)

    for (high, low, suited) in classes:
        hi_cards = by_rank.get(high, [])
        lo_cards = by_rank.get(low, [])
        if high == low:
            # Pair: all C(4,2) = 6 combos
            cards = hi_cards
            for i in range(len(cards)):
                for j in range(i + 1, len(cards)):
                    a, b = cards[i], cards[j]
                    if a in blocked_set or b in blocked_set:
                        continue
                    out.append((a, b))
        else:
            for a in hi_cards:
                for b in lo_cards:
                    if a in blocked_set or b in blocked_set:
                        continue
                    if suited and suit_of(a) != suit_of(b):
                        continue
                    if not suited and suit_of(a) == suit_of(b):
                        continue
                    out.append((a, b))
    return out
