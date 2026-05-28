"""Step-by-step math derivation for a decision.

This is the difference between *telling* the user the answer and *teaching*
them how to get it. For every decision we produce an ordered list of
derivation steps:

  Step 1: Count outs (enumerated by name, not just a number)
  Step 2: Rule of 2 and 4 → approximate equity
  Step 3: Pot odds formula → required equity
  Step 4: Compare → decide
  Step 5: EV formula → expected chips

Each step has:
  - `q`: the question (what we're computing)
  - `formula`: the equation in plain form
  - `numbers`: the actual numbers plugged in
  - `result`: the answer
  - `gloss`: a one-line explanation of WHY this matters

The client can render these as a walkthrough; in Study mode, steps are
revealed one at a time with a "show next step" button.
"""

from __future__ import annotations

from dataclasses import dataclass

from the_felt.cards import ALL_CARDS, rank_of, suit_of, to_str
from the_felt.eval import rank_class, rank_hand


_RANK_NAMES = {0:"2", 1:"3", 2:"4", 3:"5", 4:"6", 5:"7", 6:"8", 7:"9",
               8:"T", 9:"J", 10:"Q", 11:"K", 12:"A"}
_SUIT_NAMES = {1:"♠", 2:"♥", 4:"♦", 8:"♣"}


@dataclass(frozen=True, slots=True)
class DerivationStep:
    label: str          # "Outs counting" / "Pot odds" / "Rule of 4" / etc.
    q: str              # The question this step answers
    formula: str        # Plain formula like "to_call / (pot + to_call)"
    numbers: str        # The same formula with actual numbers
    result: str         # "9 outs" / "28.6%" / "+2.4 chips"
    gloss: str          # Why this matters in plain English


def derive(
    hero_cards: list[int],
    board: list[int],
    pot: int,
    to_call: int,
    equity: float,
    outs: int,
    next_card_pct: float,
    by_river_pct: float,
    street: str,
    legal_can_raise: bool,
    bb: int,
) -> list[DerivationStep]:
    """Produce a step-by-step derivation for this decision."""
    steps: list[DerivationStep] = []

    if not hero_cards:
        return steps

    # ---- Step 1: Identify the hand and board ----
    hand_str = _pretty(hero_cards)
    board_str = _pretty(board) if board else "(no community cards yet)"
    steps.append(DerivationStep(
        label="The setup",
        q="What am I working with?",
        formula="Your cards + the community cards",
        numbers=f"Hand: {hand_str}   Board: {board_str}",
        result=_describe_hand(hero_cards, board),
        gloss="Always start by naming what you have. Your range planning comes later.",
    ))

    # ---- Step 2: Count outs (only meaningful postflop) ----
    if len(board) >= 3 and outs > 0:
        out_cards = _enumerate_outs(hero_cards, board)
        steps.append(DerivationStep(
            label="Count your outs",
            q="Which cards could improve me to a winning hand?",
            formula="Count cards in the deck that lift you to a stronger hand class",
            numbers=_format_outs(out_cards),
            result=f"{outs} clean outs",
            gloss="Each \"out\" is one card that meaningfully improves you. "
                  "Conservative counting (no double-counting) is what pros use.",
        ))
        # ---- Step 3: Rule of 2 & 4 ----
        if street == "flop":
            steps.append(DerivationStep(
                label="Rule of 4 (two cards to come)",
                q="What's the chance of hitting one of those outs by the river?",
                formula="outs × 4 ≈ % chance to hit by the river",
                numbers=f"{outs} × 4 = {outs * 4}%",
                result=f"~{int(by_river_pct * 100)}% to improve by the river",
                gloss="Multiply outs by 4 on the flop to estimate equity if you'll see both turn and river. "
                      "Multiply by 2 if only one card is left.",
            ))
        elif street == "turn":
            steps.append(DerivationStep(
                label="Rule of 2 (one card to come)",
                q="What's the chance of hitting one of those outs on the river?",
                formula="outs × 2 ≈ % chance to hit on the next card",
                numbers=f"{outs} × 2 = {outs * 2}%",
                result=f"~{int(next_card_pct * 100)}% to improve",
                gloss="On the turn only one card is left, so multiply outs by 2.",
            ))

    # ---- Step 4: Equity (vs range — Monte Carlo'd by the server) ----
    steps.append(DerivationStep(
        label="Your win chance (equity)",
        q="If we ran this 10,000 times, how often would I win?",
        formula="(times you win) ÷ (total runs)",
        numbers=f"Simulated against your opponent's likely hands",
        result=f"{equity * 100:.1f}% to win at showdown",
        gloss="True equity is computed by Monte Carlo — randomly dealing the missing cards "
              "many times and counting wins. The rule-of-2/4 estimate above is a fast "
              "mental shortcut for the same number.",
    ))

    # ---- Step 5: Pot odds (only when facing a bet) ----
    if to_call > 0:
        pot_before_call = pot - to_call
        po_pct = to_call / (pot + to_call) * 100 if (pot + to_call) > 0 else 0
        steps.append(DerivationStep(
            label="Pot odds (price to call)",
            q="What's the minimum win % I need for a call to break even?",
            formula="to_call ÷ (pot + to_call)",
            numbers=f"{to_call} ÷ ({pot} + {to_call}) = {to_call} ÷ {pot + to_call} = {po_pct:.1f}%",
            result=f"You need ≥ {po_pct:.1f}% to call profitably",
            gloss="If you call X to win a pot of P, you only need to win X/(P+X) of the time "
                  "to break even. The bigger the pot relative to the bet, the lower the bar.",
        ))

        # ---- Step 6: Compare equity vs pot odds ----
        edge = equity * 100 - po_pct
        compare_result = (
            f"+EV call: you have {edge:+.1f}% edge" if edge > 0
            else f"−EV call: you're short by {abs(edge):.1f}%"
        )
        steps.append(DerivationStep(
            label="Compare",
            q="Does my equity beat the required win %?",
            formula="equity − pot_odds_required",
            numbers=f"{equity * 100:.1f}% − {po_pct:.1f}% = {edge:+.1f}%",
            result=compare_result,
            gloss="When your equity is higher than the required %, calling wins chips long-term. "
                  "When it's lower, folding does.",
        ))

        # ---- Step 7: EV in chips ----
        ev_call = equity * pot - (1 - equity) * to_call
        steps.append(DerivationStep(
            label="EV of calling, in chips",
            q="How many chips do I expect to win or lose per call, on average?",
            formula="equity × pot − (1 − equity) × to_call",
            numbers=(
                f"({equity:.2f} × {pot}) − ({1 - equity:.2f} × {to_call})  =  "
                f"{equity * pot:.1f} − {(1 - equity) * to_call:.1f}  =  {ev_call:+.1f}"
            ),
            result=f"{ev_call:+.1f} chips per call (on average)",
            gloss="This is the actual chip expectation. Make this play 100 times and you'll "
                  f"net about {ev_call * 100:+.0f} chips total.",
        ))

    else:
        # No bet to face — show value-bet math instead
        target = max(int(pot * 0.66), bb)
        # Heuristic fold equity for a value-style bet against a typical defender
        fold_eq = 0.40
        ev_bet = fold_eq * pot + (1 - fold_eq) * (equity * (pot + 2 * target) - target)
        steps.append(DerivationStep(
            label="EV of a value bet, in chips",
            q="What do I expect to win if I bet ~⅔ pot here?",
            formula="(fold% × pot) + (call% × (equity × final_pot − bet))",
            numbers=(
                f"(0.40 × {pot}) + (0.60 × ({equity:.2f} × {pot + 2 * target} − {target}))  =  "
                f"{0.40 * pot:.1f} + {0.60 * (equity * (pot + 2 * target) - target):.1f}  =  "
                f"{ev_bet:+.1f}"
            ),
            result=f"{ev_bet:+.1f} chips per bet (on average)",
            gloss="Two ways to win — they fold and you grab the pot, or they call and your "
                  "equity earns you part of the bigger pot. Both branches feed the EV.",
        ))

    return steps


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pretty(cards: list[int]) -> str:
    if not cards:
        return ""
    return " ".join(_card_pretty(c) for c in cards)


def _card_pretty(c: int) -> str:
    r = _RANK_NAMES[rank_of(c)]
    s = _SUIT_NAMES[suit_of(c)]
    return f"{r}{s}"


def _describe_hand(hero: list[int], board: list[int]) -> str:
    if len(hero) + len(board) < 5:
        if len(hero) == 2:
            r1, r2 = rank_of(hero[0]), rank_of(hero[1])
            if r1 == r2:
                return f"Pocket pair ({_RANK_NAMES[r1]}{_RANK_NAMES[r2]})"
            suited = suit_of(hero[0]) == suit_of(hero[1])
            high = _RANK_NAMES[max(r1, r2)]
            low = _RANK_NAMES[min(r1, r2)]
            return f"{high}{low} {'suited' if suited else 'offsuit'}"
        return "Pre-flop hand"
    from the_felt.eval import describe
    return describe(hero, board)


def _enumerate_outs(hero: list[int], board: list[int]) -> list[int]:
    """Return the actual card list that improves hero to a strictly better
    hand class. This is the same logic as count_outs but returns the cards."""
    if len(board) < 3:
        return []
    current_rank = rank_hand(hero, board)
    current_class = rank_class(current_rank)
    used = set(hero + board)
    remaining = [c for c in ALL_CARDS if c not in used]
    out_cards: list[int] = []
    for c in remaining:
        new_rank = rank_hand(hero, board + [c])
        new_class = rank_class(new_rank)
        if new_class < current_class:
            out_cards.append(c)
    return out_cards


def _format_outs(out_cards: list[int]) -> str:
    """Group outs by rank for readability: 'A♠ A♥ A♦ A♣' or 'A,K,Q (any suit)'."""
    if not out_cards:
        return "no clean outs"
    if len(out_cards) <= 10:
        return ", ".join(_card_pretty(c) for c in out_cards)
    # Group by rank
    by_rank: dict[int, list[int]] = {}
    for c in out_cards:
        by_rank.setdefault(rank_of(c), []).append(c)
    parts = []
    for r in sorted(by_rank.keys(), reverse=True):
        cs = by_rank[r]
        if len(cs) == 4:
            parts.append(f"any {_RANK_NAMES[r]}")
        elif len(cs) >= 2:
            parts.append(f"{len(cs)}× {_RANK_NAMES[r]}")
        else:
            parts.append(_card_pretty(cs[0]))
    return ", ".join(parts)
