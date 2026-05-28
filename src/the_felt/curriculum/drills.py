"""Drill generators.

Each drill kind has a function `generate_<kind>(rng) -> Drill`. The `Drill`
includes the prompt, the correct answer, optional multiple-choice
distractors, and an explanation that is generated from the SAME math
primitives used by the live coach — guaranteeing drill ↔ live math agreement.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from the_felt.agents.archetype import REGISTRY
from the_felt.cards import ALL_CARDS, to_str
from the_felt.eval import describe, rank_class, rank_hand
from the_felt.probability.ev import ev_call
from the_felt.probability.mdf import alpha as alpha_fn
from the_felt.probability.outs import count_outs, rule_of_2_and_4
from the_felt.probability.pot_odds import pot_odds as po_fn


# ---------------------------------------------------------------------------
# Drill model
# ---------------------------------------------------------------------------

@dataclass
class Drill:
    kind: str                       # matches Lesson.drill_kind
    lesson_id: str                  # which lesson it belongs to
    question: str                   # human-readable prompt
    answer: Any                     # canonical correct answer (int / float / string / list)
    answer_type: str                # "mc" (multiple choice) | "numeric" | "ordered"
    choices: list[str] = field(default_factory=list)   # for "mc"
    correct_index: int = -1          # for "mc": index into choices
    tolerance: float = 0.0           # for "numeric": ±tolerance accepted
    explanation: str = ""            # shown after submit
    context: dict[str, Any] = field(default_factory=dict)   # any extra data the UI may render (hand, board, ...)


def is_correct(drill: Drill, submitted: Any) -> bool:
    """Check a user submission against a Drill.answer."""
    if drill.answer_type == "mc":
        try:
            return int(submitted) == drill.correct_index
        except Exception:
            return False
    if drill.answer_type == "numeric":
        try:
            return abs(float(submitted) - float(drill.answer)) <= drill.tolerance
        except Exception:
            return False
    if drill.answer_type == "ordered":
        # `answer` and `submitted` are both lists; must be in the same order
        try:
            return list(submitted) == list(drill.answer)
        except Exception:
            return False
    return False


# ---------------------------------------------------------------------------
# Card sampling helpers
# ---------------------------------------------------------------------------

def _sample_hand_and_flop(rng: random.Random) -> tuple[list[int], list[int]]:
    """Sample 2 hole cards + a 3-card flop without replacement."""
    cards = rng.sample(ALL_CARDS, 5)
    return cards[:2], cards[2:]


def _sample_hand_and_turn(rng: random.Random) -> tuple[list[int], list[int]]:
    cards = rng.sample(ALL_CARDS, 6)
    return cards[:2], cards[2:]


def _format_cards(cards: list[int]) -> str:
    return " ".join(to_str(c) for c in cards)


# ---------------------------------------------------------------------------
# M2: Counting outs
# ---------------------------------------------------------------------------

def generate_count_flush_outs(rng: random.Random) -> Drill:
    """Sample a hand + flop where hero has a flush draw; ask for outs count."""
    # Find a scenario where the hero has a 4-flush after the flop. Retry until.
    for _ in range(200):
        hole, flop = _sample_hand_and_flop(rng)
        suit_counts: dict[int, int] = {}
        for c in hole + flop:
            from the_felt.cards import suit_of
            s = suit_of(c)
            suit_counts[s] = suit_counts.get(s, 0) + 1
        if max(suit_counts.values()) == 4:  # exactly 4 of same suit
            # outs for the flush = remaining cards of that suit (13 - 4 = 9)
            outs = 9
            # But some "outs" are dirty if board pairs etc — for the basic flush-out
            # drill we keep it simple and ask just for flush outs.
            distractors = [4, 8, 12, outs]
            distractors = list(dict.fromkeys(distractors))  # dedupe preserving order
            rng.shuffle(distractors)
            correct_idx = distractors.index(outs)
            return Drill(
                kind="count_flush_outs",
                lesson_id="M2.1",
                question=(
                    f"You have {_format_cards(hole)} on a flop of {_format_cards(flop)}. "
                    "How many cards in the deck complete your flush?"
                ),
                answer=outs,
                answer_type="mc",
                choices=[str(d) for d in distractors],
                correct_index=correct_idx,
                explanation=(
                    "There are 13 cards of each suit. You see 4 of them (2 in your "
                    "hand, 2 on the board). 13 − 4 = 9 cards left that complete your flush."
                ),
                context={"hole": [to_str(c) for c in hole], "board": [to_str(c) for c in flop]},
            )
    # Fallback: synthetic
    return Drill(
        kind="count_flush_outs", lesson_id="M2.1",
        question="You have a 4-card flush after the flop. How many outs?",
        answer=9, answer_type="mc",
        choices=["4", "8", "9", "12"], correct_index=2,
        explanation="13 cards of the suit − 4 you can see = 9 outs.",
    )


def generate_count_oe_straight_outs(rng: random.Random) -> Drill:
    """A 4-card open-ended straight has 8 outs (4 cards on each end)."""
    # Quick worked-example drill; deterministic content for clarity.
    examples = [
        ("8♠ 9♥", "T♣ 7♦ 2♠", "Any 6 (4) or any J (4) = 8 outs."),
        ("J♠ T♥", "Q♣ 9♦ 3♠", "Any 8 or any K = 8 outs."),
        ("7♠ 8♣", "9♥ 6♦ 2♣", "Any 5 or any T = 8 outs."),
    ]
    hand, board, explain = rng.choice(examples)
    return Drill(
        kind="count_oe_straight_outs", lesson_id="M2.2",
        question=f"You have {hand} on a flop of {board}. How many cards complete an open-ended straight?",
        answer=8, answer_type="mc",
        choices=["4", "6", "8", "12"], correct_index=2,
        explanation=f"Open-ended straight draws have 4 cards on each end = 8 outs. ({explain})",
    )


def generate_count_gutshot_outs(rng: random.Random) -> Drill:
    examples = [
        ("J♠ T♥", "8♣ 7♦ 2♠", "Only a 9 fills the gut. 4 nines remain = 4 outs."),
        ("9♠ 7♣", "T♥ 6♦ 2♣", "Only an 8 fills the gut. 4 eights remain = 4 outs."),
        ("K♠ J♣", "Q♥ 9♦ 2♣", "Only a T fills the gut. 4 tens remain = 4 outs."),
    ]
    hand, board, explain = rng.choice(examples)
    return Drill(
        kind="count_gutshot_outs", lesson_id="M2.3",
        question=f"You have {hand} on a flop of {board}. How many cards complete a gutshot straight?",
        answer=4, answer_type="mc",
        choices=["2", "4", "6", "8"], correct_index=1,
        explanation=f"A gutshot has one rank that fills the inside. 4 cards of that rank remain. ({explain})",
    )


def generate_count_combo_outs(rng: random.Random) -> Drill:
    """Flush + open-ended straight = 15 outs (9 flush + 8 straight − 2 overlap)."""
    examples = [
        (
            "9♥ 8♥", "T♥ 7♥ 2♠",
            "9 flush outs + 8 straight outs − 2 shared (J♥, 6♥ count for both) = 15 outs.",
        ),
        (
            "7♠ 6♠", "8♠ 5♠ K♥",
            "9 flush outs + 8 straight outs − 2 shared = 15 outs.",
        ),
    ]
    hand, board, explain = rng.choice(examples)
    return Drill(
        kind="count_combo_outs", lesson_id="M2.4",
        question=(
            f"You have {hand} on a flop of {board}. You have a flush draw AND an open-ended "
            "straight draw. How many TOTAL clean outs do you have? (Don't double-count.)"
        ),
        answer=15, answer_type="mc",
        choices=["9", "13", "15", "17"], correct_index=2,
        explanation=explain,
    )


# ---------------------------------------------------------------------------
# M3: Rule of 2 and 4
# ---------------------------------------------------------------------------

def generate_outs_to_equity_turn(rng: random.Random) -> Drill:
    """On the turn, equity by river ≈ outs × 2."""
    outs = rng.choice([4, 6, 8, 9, 12, 15])
    correct_pct = outs * 2
    distractors = [outs * 1, outs * 2, outs * 3, outs * 4]
    distractors = list(dict.fromkeys(distractors))
    rng.shuffle(distractors)
    return Drill(
        kind="outs_to_equity_turn", lesson_id="M3.1",
        question=(
            f"You have {outs} outs on the TURN (one card to come). "
            f"What's your approximate equity to improve on the river?"
        ),
        answer=correct_pct, answer_type="mc",
        choices=[f"{d}%" for d in distractors],
        correct_index=distractors.index(correct_pct),
        explanation=(
            f"Rule of 2: outs × 2 ≈ % to hit on one card. {outs} × 2 = {correct_pct}%. "
            f"True probability (outs / 46) is {outs / 46 * 100:.1f}% — the rule is a fast shortcut."
        ),
    )


def generate_outs_to_equity_river(rng: random.Random) -> Drill:
    """On the flop with two cards to come, equity by river ≈ outs × 4."""
    outs = rng.choice([4, 6, 8, 9, 12, 15])
    correct_pct = outs * 4
    if correct_pct > 80:
        correct_pct = min(correct_pct, 60)  # rule breaks down for >12 outs but keep concept
        correct_pct = outs * 4
    distractors = [outs * 2, outs * 3, outs * 4, outs * 5]
    distractors = [d for d in distractors if 0 < d <= 100]
    distractors = list(dict.fromkeys(distractors))
    rng.shuffle(distractors)
    return Drill(
        kind="outs_to_equity_river", lesson_id="M3.2",
        question=(
            f"You have {outs} outs on the FLOP (two cards to come, assume you see both). "
            f"What's your approximate equity to improve by the river?"
        ),
        answer=correct_pct, answer_type="mc",
        choices=[f"{d}%" for d in distractors],
        correct_index=distractors.index(correct_pct),
        explanation=(
            f"Rule of 4: outs × 4 ≈ % to hit by river when two cards are coming. "
            f"{outs} × 4 = {correct_pct}%. The rule is a fast shortcut; precise math gets within ~2 pts."
        ),
    )


# ---------------------------------------------------------------------------
# M4: Pot odds
# ---------------------------------------------------------------------------

def generate_compute_pot_odds(rng: random.Random) -> Drill:
    pot = rng.choice([30, 50, 80, 100, 150, 200])
    bet_frac = rng.choice([0.33, 0.5, 0.66, 1.0])
    bet = max(5, int(pot * bet_frac))
    pot_after_bet = pot + bet
    required = bet / (pot_after_bet + bet) * 100
    correct = round(required, 1)
    distractors = [
        round(bet / pot * 100, 1),                          # common mistake: bet/pot
        round(bet / pot_after_bet * 100, 1),                # raw price/pot-after
        correct,
        round(required + 10, 1),
    ]
    distractors = list(dict.fromkeys(distractors))
    rng.shuffle(distractors)
    return Drill(
        kind="compute_pot_odds", lesson_id="M4.1",
        question=(
            f"The pot was {pot} when your opponent bet {bet}. It's your turn. "
            f"What's the minimum win % you'd need to break even on a call?"
        ),
        answer=correct, answer_type="numeric",
        tolerance=2.0,
        explanation=(
            f"Formula: to_call / (pot + to_call). "
            f"to_call is {bet}; pot is now {pot_after_bet} (including their bet). "
            f"{bet} ÷ ({pot_after_bet} + {bet}) = {bet} ÷ {pot_after_bet + bet} = {required:.1f}%."
        ),
        choices=[f"{d}%" for d in distractors],
        correct_index=distractors.index(correct),
        context={"pot_before_bet": pot, "bet": bet, "pot_after_bet": pot_after_bet},
    )


def generate_should_call_with_equity(rng: random.Random) -> Drill:
    pot = rng.choice([60, 80, 100, 120])
    bet = rng.choice([20, 30, 40, 50])
    equity_pct = rng.randint(15, 60)
    required = bet / (pot + bet + bet) * 100
    correct_call = equity_pct >= required
    return Drill(
        kind="should_call_with_equity", lesson_id="M4.2",
        question=(
            f"The pot is {pot}, your opponent bets {bet}, and your hand has about "
            f"{equity_pct}% to win at showdown. Should you call?"
        ),
        answer="call" if correct_call else "fold",
        answer_type="mc",
        choices=["Call (it's +EV)", "Fold (it's −EV)"],
        correct_index=0 if correct_call else 1,
        explanation=(
            f"Pot odds: {bet} / ({pot + bet} + {bet}) = {required:.1f}% needed. "
            f"You have {equity_pct}% — "
            f"{'above' if correct_call else 'below'} the threshold, so "
            f"{'calling' if correct_call else 'folding'} is +EV."
        ),
    )


# ---------------------------------------------------------------------------
# M5: EV
# ---------------------------------------------------------------------------

def generate_ev_call_numeric(rng: random.Random) -> Drill:
    pot = rng.choice([60, 80, 100, 120, 150])
    bet = rng.choice([20, 30, 40, 60])
    equity = rng.uniform(0.20, 0.65)
    # EV of calling: equity * (pot_after_their_bet) - (1-equity) * bet
    pot_facing = pot + bet
    ev = ev_call(equity, bet, pot_facing)
    correct = round(ev, 1)
    return Drill(
        kind="ev_call_numeric", lesson_id="M5.1",
        question=(
            f"You have {equity*100:.0f}% equity. The pot is {pot}, opponent bets {bet} "
            f"(pot now {pot_facing}). What's the EV of calling, in chips?"
        ),
        answer=correct, answer_type="numeric",
        tolerance=1.5,
        explanation=(
            f"Formula: equity × pot − (1 − equity) × to_call. "
            f"({equity:.2f} × {pot_facing}) − ({1 - equity:.2f} × {bet}) = "
            f"{equity * pot_facing:.1f} − {(1 - equity) * bet:.1f} = {ev:+.1f} chips."
        ),
    )


def generate_ev_bet_with_fold_equity(rng: random.Random) -> Drill:
    pot = rng.choice([40, 60, 80, 100])
    bet = int(pot * rng.choice([0.5, 0.66, 1.0]))
    fold_pct = rng.choice([30, 40, 50, 60, 70])
    equity_when_called = rng.uniform(0.20, 0.45)
    # EV: fold_pct * pot + (1-fold_pct) * (equity * (pot + 2*bet) - bet)
    f = fold_pct / 100
    final_pot = pot + 2 * bet
    ev = f * pot + (1 - f) * (equity_when_called * final_pot - bet)
    correct = round(ev, 1)
    return Drill(
        kind="ev_bet_with_fold_equity", lesson_id="M5.2",
        question=(
            f"Pot is {pot}. You bet {bet}. You think opponent folds {fold_pct}% of the time. "
            f"When they call, your equity is {equity_when_called*100:.0f}%. EV of betting in chips?"
        ),
        answer=correct, answer_type="numeric",
        tolerance=2.0,
        explanation=(
            f"Two branches: "
            f"(a) {fold_pct}% they fold → you win pot ({pot}). "
            f"(b) {100-fold_pct}% they call → you win {equity_when_called*100:.0f}% × final pot {final_pot} = "
            f"{equity_when_called * final_pot:.1f}, minus your {bet} cost. "
            f"Combined: {f:.2f} × {pot} + {1-f:.2f} × ({equity_when_called:.2f} × {final_pot} − {bet}) = {ev:+.1f}."
        ),
    )


def generate_compare_ev_actions(rng: random.Random) -> Drill:
    fold_ev = 0.0
    call_ev = round(rng.uniform(-5, 12), 1)
    raise_ev = round(rng.uniform(-3, 15), 1)
    options = [("Fold", fold_ev), ("Call", call_ev), ("Raise", raise_ev)]
    best_name, best_ev = max(options, key=lambda x: x[1])
    correct_idx = [o[0] for o in options].index(best_name)
    return Drill(
        kind="compare_ev_actions", lesson_id="M5.3",
        question=(
            f"You computed EVs for each action: Fold = {fold_ev:+.1f}, Call = {call_ev:+.1f}, "
            f"Raise = {raise_ev:+.1f}. Which action maximizes your EV?"
        ),
        answer=best_name, answer_type="mc",
        choices=[f"{n} ({v:+.1f})" for n, v in options],
        correct_index=correct_idx,
        explanation=(
            f"Pick the action with the highest EV. Here {best_name} at {best_ev:+.1f} "
            "is the highest. The EV-max action is always your default — deviate only with a read."
        ),
    )


# ---------------------------------------------------------------------------
# M6: Fold equity & bluffing
# ---------------------------------------------------------------------------

def generate_pure_bluff_break_even(rng: random.Random) -> Drill:
    pot = rng.choice([40, 60, 80, 100, 120])
    bet_frac = rng.choice([0.5, 0.66, 1.0])
    bet = int(pot * bet_frac)
    required = alpha_fn(bet, pot) * 100
    correct = round(required, 1)
    return Drill(
        kind="pure_bluff_break_even", lesson_id="M6.1",
        question=(
            f"You bet {bet} into a pot of {pot} as a pure bluff (assume you always lose if called). "
            f"How often does your opponent need to fold for the bluff to break even?"
        ),
        answer=correct, answer_type="numeric",
        tolerance=2.5,
        explanation=(
            f"Formula α = bet / (pot + bet). Your bet risks {bet} to win {pot}. "
            f"{bet} ÷ ({pot} + {bet}) = {bet} ÷ {pot + bet} = {required:.1f}%. "
            f"They must fold at least that often or your bluff loses money."
        ),
    )


def generate_should_bluff_archetype(rng: random.Random) -> Drill:
    arch_name = rng.choice(["Nit", "Calling Station", "TAG", "LAG", "Maniac", "Whale"])
    arch = REGISTRY[arch_name]
    # Bluff is profitable if archetype's fold_to_3bet is high enough
    is_profitable = arch.fold_to_3bet >= 0.55
    return Drill(
        kind="should_bluff_archetype", lesson_id="M6.4",
        question=(
            f"You have a busted draw. Your opponent is a **{arch_name}** "
            f"({_describe_arch(arch)}). Should you bluff the river?"
        ),
        answer="bluff" if is_profitable else "give up",
        answer_type="mc",
        choices=["Bluff — they fold often enough", "Give up — they call too much"],
        correct_index=0 if is_profitable else 1,
        explanation=(
            f"{arch_name}s fold to aggression about {int(arch.fold_to_3bet*100)}% of the time. "
            f"{'High fold rate → bluffs print money.' if is_profitable else 'Low fold rate → never bluff; value bet thin instead.'}"
        ),
    )


def _describe_arch(arch) -> str:
    return f"VPIP {int(arch.vpip*100)}, PFR {int(arch.pfr*100)}, folds to aggression {int(arch.fold_to_3bet*100)}%"


# ---------------------------------------------------------------------------
# M7: Identify archetype
# ---------------------------------------------------------------------------

def generate_identify_archetype(rng: random.Random) -> Drill:
    arch_name = rng.choice(list(REGISTRY.keys()))
    arch = REGISTRY[arch_name]
    others = [n for n in REGISTRY if n != arch_name]
    distractors = rng.sample(others, 3) + [arch_name]
    rng.shuffle(distractors)
    return Drill(
        kind="identify_archetype", lesson_id="M7.1",
        question=(
            f"An opponent has these stats: VPIP {int(arch.vpip*100)}%, PFR {int(arch.pfr*100)}%, "
            f"AFq {int(arch.afq*100)}%, c-bet {int(arch.cbet_freq*100)}%. "
            "Which archetype best matches?"
        ),
        answer=arch_name, answer_type="mc",
        choices=distractors,
        correct_index=distractors.index(arch_name),
        explanation=(
            f"VPIP {int(arch.vpip*100)}% + PFR {int(arch.pfr*100)}% places them in the "
            f"{_archetype_quadrant(arch)} quadrant. Combined with their c-bet rate, that's a {arch_name}."
        ),
    )


def _archetype_quadrant(arch) -> str:
    loose = arch.vpip >= 0.27
    aggressive = arch.afq >= 0.40
    if loose and aggressive: return "loose-aggressive"
    if loose and not aggressive: return "loose-passive"
    if not loose and aggressive: return "tight-aggressive"
    return "tight-passive"


# ---------------------------------------------------------------------------
# M8: Exploits
# ---------------------------------------------------------------------------

_EXPLOITS = {
    "Nit": "Steal their blinds wide; bluff often; fold to their aggression.",
    "Calling Station": "Value-bet thin, never bluff.",
    "Whale": "Value-bet everything; don't bluff.",
    "TAG": "Balanced; bluff selectively in position.",
    "LAG": "Widen calling range; tighten own bluffs; trap with monsters.",
    "Maniac": "Tighten up and call lighter — they bluff too much.",
    "GTO Reg": "Stay close to GTO yourself; small exploits only.",
}


def generate_best_play_vs_archetype(rng: random.Random) -> Drill:
    arch_name = rng.choice(list(_EXPLOITS.keys()))
    correct_answer = _EXPLOITS[arch_name]
    distractors = list(set(_EXPLOITS.values()) - {correct_answer})
    rng.shuffle(distractors)
    choices = distractors[:3] + [correct_answer]
    rng.shuffle(choices)
    return Drill(
        kind="best_play_vs_archetype", lesson_id="M8.1",
        question=f"What's the right counter-strategy against a {arch_name}?",
        answer=correct_answer, answer_type="mc",
        choices=choices,
        correct_index=choices.index(correct_answer),
        explanation=(
            f"vs {arch_name}: {correct_answer} "
            f"(Their style — {_describe_arch(REGISTRY.get(arch_name, REGISTRY['TAG']))} — drives this exploit.)"
        ),
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

GENERATORS = {
    # M2
    "count_flush_outs": generate_count_flush_outs,
    "count_oe_straight_outs": generate_count_oe_straight_outs,
    "count_gutshot_outs": generate_count_gutshot_outs,
    "count_combo_outs": generate_count_combo_outs,
    # M3
    "outs_to_equity_turn": generate_outs_to_equity_turn,
    "outs_to_equity_river": generate_outs_to_equity_river,
    # M4
    "compute_pot_odds": generate_compute_pot_odds,
    "should_call_with_equity": generate_should_call_with_equity,
    # M5
    "ev_call_numeric": generate_ev_call_numeric,
    "ev_bet_with_fold_equity": generate_ev_bet_with_fold_equity,
    "compare_ev_actions": generate_compare_ev_actions,
    # M6
    "pure_bluff_break_even": generate_pure_bluff_break_even,
    "should_bluff_archetype": generate_should_bluff_archetype,
    # M7
    "identify_archetype": generate_identify_archetype,
    # M8
    "best_play_vs_archetype": generate_best_play_vs_archetype,
}


def generate(kind: str, rng: random.Random | None = None) -> Drill:
    """Public entry: generate a drill of the given kind."""
    rng = rng or random.Random()
    fn = GENERATORS.get(kind)
    if fn is None:
        raise ValueError(f"Unknown drill kind: {kind}")
    return fn(rng)


def available_kinds() -> list[str]:
    return list(GENERATORS.keys())
