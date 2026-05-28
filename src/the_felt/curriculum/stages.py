"""Pilot-style training stages.

The student learns one control axis at a time. Each stage adds a new
*type* of question they must answer on their turn; earlier stages stay
active but are graded automatically by the app.

Adaptive frequency:
  - At stage start, the modal fires on every meaningful decision.
  - After consecutive correct answers, frequency drops (5+ → 0.66,
    10+ → 0.33, 15+ → 0.20).
  - Any wrong answer resets to 1.0.
  - After 5 *clean hands* (every quiz answered correctly) at frequency
    ≤ 0.33, prompt the user to graduate to the next stage.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Stage definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class StageQuestion:
    """One quiz question shown in the per-turn modal."""

    id: str               # "outs" / "pot_odds" / "ev_call" / ...
    prompt: str           # human-readable
    answer_type: str      # "mc" | "numeric"
    hint: str             # one-line nudge
    formula: str = ""     # optional formula reminder for the "show me" reveal


@dataclass(frozen=True, slots=True)
class WalkthroughStep:
    """One slide in the pre-stage walkthrough that teaches the concept."""

    title: str            # e.g. "What's an out?"
    body: str             # html-safe explanation
    formula: str = ""     # optional formula line
    # Worked example with cards: if set, the modal renders a felt-style
    # situation card with these cards and the noted answer.
    example_hole: tuple[str, ...] = ()      # e.g. ("9s", "8s")
    example_board: tuple[str, ...] = ()     # e.g. ("Ks", "7h", "4s")
    example_answer: str = ""                # "9 flush outs = 9 spades − 4 visible"


@dataclass(frozen=True, slots=True)
class TrainingStage:
    """One pilot-style training stage."""

    id: int               # 1..8
    title: str
    teaches: str          # what control axis this stage unlocks
    questions: tuple[StageQuestion, ...]
    handled_for_you: tuple[str, ...]  # which axes the app still handles automatically
    intro: str            # shown when user enters this stage for the first time
    walkthrough: tuple[WalkthroughStep, ...] = ()   # pre-quiz lesson slides


_STAGE_QUESTIONS = {
    "hand_class":  StageQuestion(
        id="hand_class",
        prompt="What hand do you currently have?",
        answer_type="mc",
        hint="Look at your 2 hole cards combined with the board. What 5-card combo is your best?",
        formula="Best 5 of (your 2 + community cards)",
    ),
    "outs": StageQuestion(
        id="outs",
        prompt="How many clean outs do you have?",
        answer_type="numeric",
        hint="Cards left in the deck that lift you to a better hand class (don't double-count).",
        formula="Outs to flush = (13 − suit cards you see). OE straight = 8. Gutshot = 4.",
    ),
    "pot_odds": StageQuestion(
        id="pot_odds",
        prompt="What's the price to call (% you need to win to break even)?",
        answer_type="numeric",
        hint="Divide what you'd pay by the total pot after your call.",
        formula="pot_odds = to_call ÷ (pot + to_call)",
    ),
    "should_call": StageQuestion(
        id="should_call",
        prompt="Given your equity and the price, should you continue?",
        answer_type="mc",
        hint="If your equity ≥ pot odds required, calling makes money.",
        formula="continue iff equity ≥ pot_odds_required",
    ),
    "ev_call": StageQuestion(
        id="ev_call",
        prompt="What's the EV of calling, in chips?",
        answer_type="numeric",
        hint="Equity × pot − (1 − equity) × call.",
        formula="EV_call = equity × pot − (1 − equity) × to_call",
    ),
    "best_action": StageQuestion(
        id="best_action",
        prompt="Which action has the highest EV?",
        answer_type="mc",
        hint="Compare the EV per action — the trainer pre-computed each one.",
        formula="argmax(EV) over {fold, call, raise, ...}",
    ),
    "position_aware": StageQuestion(
        id="position_aware",
        prompt="Are you in position vs the bettor, and does that loosen or tighten your continue range?",
        answer_type="mc",
        hint="Acting last = more info = wider continue range. Acting first = tighter.",
        formula="IP → defend wider; OOP → defend tighter",
    ),
    "villain_range": StageQuestion(
        id="villain_range",
        prompt="Roughly what fraction of all hands is the villain playing right now?",
        answer_type="mc",
        hint="Tighter players = narrower range. Open from early position = top 12-18%. Button steal = top 35-45%.",
        formula="range % depends on archetype × position × action",
    ),
    "archetype_id": StageQuestion(
        id="archetype_id",
        prompt="What archetype is this player?",
        answer_type="mc",
        hint="Cross VPIP × AFq: tight/loose × passive/aggressive gives the quadrant.",
        formula="loose-aggressive = LAG; loose-passive = Whale/Station; tight-aggressive = TAG; tight-passive = Nit",
    ),
    "exploit": StageQuestion(
        id="exploit",
        prompt="What's the right exploit-aware play here?",
        answer_type="mc",
        hint="vs Nit: steal more. vs Station: value-bet, no bluffs. vs Maniac: tighten + trap. vs TAG: balanced.",
        formula="exploit = counter-strategy specific to opponent archetype",
    ),
}


# ---------------------------------------------------------------------------
# Walkthrough content — what each stage teaches BEFORE we start quizzing
# ---------------------------------------------------------------------------

_WT_STAGE1 = (
    WalkthroughStep(
        title="Reading your hand",
        body=("Every Hold'em hand has a 5-card poker rank built from your 2 hole cards plus "
              "the community cards on the board. Your job is to spot the BEST 5-card "
              "combination available right now."),
        formula="Best 5 of (2 hole + up to 5 board)",
    ),
    WalkthroughStep(
        title="Hand class ladder (strongest → weakest)",
        body=("Royal flush → Straight flush → Four of a kind → Full house → Flush → "
              "Straight → Three of a kind → Two pair → Pair → High card. Memorize this order."),
    ),
    WalkthroughStep(
        title="Worked example",
        body=("You hold 9♠ 8♠. The board is K♠ 7♥ 4♠. You have FOUR spades (9♠, 8♠, K♠, 4♠) — "
              "but only 4, not 5. That's a flush <em>draw</em>, not a made flush. Your current "
              "hand is just a high card (king-high)."),
        example_hole=("9s", "8s"),
        example_board=("Ks", "7h", "4s"),
        example_answer="Current best hand: High card (K-high)",
    ),
    WalkthroughStep(
        title="What you'll do",
        body=("On each turn we'll show you your cards and ask: 'What hand do you have right now?' "
              "Pick from the 9 hand classes. We handle everything else (outs, equity, EV, "
              "position, ranges, archetype, recommended action) while you build this single reflex."),
    ),
)

_WT_STAGE2 = (
    WalkthroughStep(
        title="What's an out?",
        body=("An <strong>out</strong> is a single card left in the deck that turns your "
              "hand into a hand class STRONGER than what you have now. We don't count "
              "kicker-only improvements — only true class jumps (pair → two pair, draw → flush, etc.)."),
    ),
    WalkthroughStep(
        title="Common out counts to memorize",
        body=("<strong>9</strong> — flush draw (13 of suit − 4 you see).<br>"
              "<strong>8</strong> — open-ended straight (4 on each end).<br>"
              "<strong>4</strong> — gutshot (one rank fills the inside).<br>"
              "<strong>2</strong> — set draw (2 remaining of your pair's rank).<br>"
              "<strong>5</strong> — pair → two-pair or trips (3 + 2)."),
    ),
    WalkthroughStep(
        title="Pure flush draw",
        body=("You hold 9♠ 8♠ on a flop of K♠ 7♥ 4♠. You see 4 spades total (2 in your hand, "
              "2 on the board). 13 spades exist in the deck, so 13 − 4 = <strong>9 flush outs</strong>."),
        example_hole=("9s", "8s"),
        example_board=("Ks", "7h", "4s"),
        example_answer="9 outs (any remaining spade completes the flush)",
        formula="flush outs = 13 − (spades you see)",
    ),
    WalkthroughStep(
        title="Combo draw — DON'T double-count",
        body=("You hold 9♥ 8♥ on T♥ 7♥ 2♠. Flush draw = 9. Open-ended straight = 8 (any 6, any J). "
              "But the J♥ and 6♥ count for BOTH draws — subtract those 2 shared cards. "
              "Total clean outs = 9 + 8 − 2 = <strong>15</strong>."),
        example_hole=("9h", "8h"),
        example_board=("Th", "7h", "2s"),
        example_answer="15 outs (9 flush + 8 straight − 2 shared)",
        formula="combined = flush + straight − shared overlap",
    ),
    WalkthroughStep(
        title="What you'll do",
        body=("Each turn that involves a draw, we'll ask 'how many clean outs?' Type the number. "
              "If you're off, we'll show you the actual cards bucketed by what they make so you "
              "see exactly which outs you missed."),
    ),
)

_WT_STAGE3 = (
    WalkthroughStep(
        title="What are pot odds?",
        body=("When someone bets, you're being offered a PRICE to keep playing. Pot odds tell you "
              "the minimum win % your hand needs in order for calling to break even over the "
              "long run."),
        formula="required win % = to_call ÷ (pot + to_call)",
    ),
    WalkthroughStep(
        title="Worked example",
        body=("Pot was 60. Opponent bets 30. The pot is now 90. You'd pay 30 to win 90. "
              "30 ÷ (90 + 30) = 30 ÷ 120 = <strong>25% needed</strong>. If your hand wins MORE than "
              "25% of the time, calling is profitable."),
        example_answer="Need ≥ 25% to break even on the call",
    ),
    WalkthroughStep(
        title="Decision rule",
        body=("Compare YOUR equity (we still compute it for you) to the pot odds you just calculated. "
              "Equity ≥ pot odds → call. Equity < pot odds → fold. That's the entire 'should I call' "
              "math reduced to one comparison."),
    ),
)

_WT_STAGE4 = (
    WalkthroughStep(
        title="From percentages to chips",
        body=("EV (expected value) translates win-rate into ACTUAL CHIPS gained or lost per "
              "decision. Positive EV makes you money over thousands of hands even if any single "
              "hand can lose to variance."),
        formula="EV(call) = equity × pot − (1 − equity) × to_call",
    ),
    WalkthroughStep(
        title="Worked example",
        body=("You have 40% equity. The pot is 80 and you'd call 30. "
              "EV = 0.40 × 80 − 0.60 × 30 = 32 − 18 = <strong>+14 chips</strong> per call on average. "
              "Even though you lose 60% of the time, the long-run EV is positive."),
        example_answer="EV(call) = +14 chips",
    ),
    WalkthroughStep(
        title="Picking the best action",
        body=("EV(fold) is always 0. EV(call), EV(raise) etc. depend on equity and sizing. "
              "The right play is whichever action has the highest EV. We pre-compute these "
              "for you; you'll learn to pick the max."),
    ),
)

_WT_STAGE5 = (
    WalkthroughStep(
        title="Position = information",
        body=("On every street, players act in order. Whoever acts LAST sees what everyone else "
              "did before making their decision. That information advantage is huge — it lets you "
              "play more hands profitably."),
    ),
    WalkthroughStep(
        title="In position vs out of position",
        body=("<strong>IP</strong> (in position): you act last → defend wider, bluff more, "
              "realize more of your equity.<br>"
              "<strong>OOP</strong> (out of position): you act first → defend tighter; you'll be "
              "guessing what they have on every street."),
    ),
)

_WT_STAGE6 = (
    WalkthroughStep(
        title="Range thinking",
        body=("A player doesn't have ONE hand — they have a DISTRIBUTION of hands consistent "
              "with their actions. A tight UTG open might mean QQ+, AK, AQs (about 5% of all "
              "hands). A loose button open might be 35% of all hands. Compare your equity vs the "
              "range, not vs one guess."),
    ),
)

_WT_STAGE7 = (
    WalkthroughStep(
        title="The 4 quadrants of players",
        body=("Players cluster into types based on two axes: tight/loose (how many hands they "
              "play) and passive/aggressive (how often they bet/raise vs call). "
              "Tight+Aggressive = TAG. Loose+Aggressive = LAG. Loose+Passive = Calling Station / "
              "Whale. Tight+Passive = Nit."),
    ),
)

_WT_STAGE8 = (
    WalkthroughStep(
        title="Adjust to the opponent",
        body=("Each archetype has a counter-strategy:<br>"
              "<strong>Nit</strong>: steal more, fold to their raises.<br>"
              "<strong>Calling Station</strong>: value-bet thin, never bluff.<br>"
              "<strong>Maniac</strong>: tighten + call lighter, don't bluff back.<br>"
              "<strong>TAG</strong>: balanced — bluff selectively in position."),
    ),
)


STAGES: tuple[TrainingStage, ...] = (
    TrainingStage(
        id=1, title="Read your hand", teaches="hand classes",
        questions=(_STAGE_QUESTIONS["hand_class"],),
        handled_for_you=("outs", "equity", "pot_odds", "EV", "position", "range", "archetype", "exploit"),
        intro=("Welcome to the simulator. We'll fly the helicopter for you on every "
               "control except hand reading. Tell us what hand you have on each turn "
               "and we'll handle the rest."),
        walkthrough=_WT_STAGE1,
    ),
    TrainingStage(
        id=2, title="Count your outs", teaches="outs counting",
        questions=(_STAGE_QUESTIONS["outs"],),
        handled_for_you=("equity", "pot_odds", "EV", "position", "range", "archetype", "exploit"),
        intro=("New axis unlocked: outs counting. We'll still compute equity, pot odds, "
               "and tell you what to do — but you have to count the cards that help you."),
        walkthrough=_WT_STAGE2,
    ),
    TrainingStage(
        id=3, title="Compute pot odds", teaches="pot odds + should-I-call",
        questions=(_STAGE_QUESTIONS["pot_odds"], _STAGE_QUESTIONS["should_call"]),
        handled_for_you=("equity", "EV", "position", "range", "archetype", "exploit"),
        intro=("Now you compute the price to call yourself. We still tell you your "
               "equity — your job is to compare and decide."),
        walkthrough=_WT_STAGE3,
    ),
    TrainingStage(
        id=4, title="EV in chips", teaches="expected value",
        questions=(_STAGE_QUESTIONS["ev_call"], _STAGE_QUESTIONS["best_action"]),
        handled_for_you=("position", "range", "archetype", "exploit"),
        intro=("Stop thinking in percentages — think in chips. EV per decision is what "
               "actually compounds over thousands of hands."),
        walkthrough=_WT_STAGE4,
    ),
    TrainingStage(
        id=5, title="Position", teaches="positional awareness",
        questions=(_STAGE_QUESTIONS["position_aware"],),
        handled_for_you=("range", "archetype", "exploit"),
        intro=("Position is the most underrated edge in poker. Acting last is a free "
               "structural advantage you should price into every decision."),
        walkthrough=_WT_STAGE5,
    ),
    TrainingStage(
        id=6, title="Ranges", teaches="range-vs-range thinking",
        questions=(_STAGE_QUESTIONS["villain_range"],),
        handled_for_you=("archetype", "exploit"),
        intro=("Stop asking 'what does he have?' Start asking 'what's his distribution?' "
               "Equity vs a range is the real metric — equity vs a single hand is theatre."),
        walkthrough=_WT_STAGE6,
    ),
    TrainingStage(
        id=7, title="Opponent typing", teaches="archetype reading",
        questions=(_STAGE_QUESTIONS["archetype_id"],),
        handled_for_you=("exploit",),
        intro=("Different players need different counter-strategies. Tag every opponent "
               "with an archetype label within 15 hands of watching them."),
        walkthrough=_WT_STAGE7,
    ),
    TrainingStage(
        id=8, title="Exploits + free flight", teaches="opponent-specific exploits",
        questions=(_STAGE_QUESTIONS["exploit"],),
        handled_for_you=(),
        intro=("Final axis. You're flying solo now — no recommendations from the app. "
               "Pick the exploit-aware play and own the outcome."),
        walkthrough=_WT_STAGE8,
    ),
)


def get_walkthrough(stage_id: int) -> list[dict]:
    """Return a JSON-serializable walkthrough for the given stage."""
    stage = get_stage(stage_id)
    return [
        {
            "title": w.title,
            "body": w.body,
            "formula": w.formula,
            "example_hole": list(w.example_hole),
            "example_board": list(w.example_board),
            "example_answer": w.example_answer,
        }
        for w in stage.walkthrough
    ]


# ---------------------------------------------------------------------------
# Adaptive frequency state per user
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class StageState:
    """Per-user runtime state for the active stage."""

    stage_id: int = 1
    correct_streak: int = 0       # consecutive correct quiz answers in this stage
    clean_hands: int = 0          # consecutive hands with no wrong quiz answers
    this_hand_clean: bool = True  # reset to True at hand start, False on any wrong answer
    quiz_count: int = 0           # total quizzes asked (this stage)
    correct_count: int = 0         # total correct answers (this stage)

    def reset_hand(self) -> None:
        # Called at the start of each new hand. Bump clean_hands if last hand was perfect.
        if self.this_hand_clean and self.quiz_count > 0:
            self.clean_hands += 1
        self.this_hand_clean = True

    def record_quiz_result(self, correct: bool) -> None:
        self.quiz_count += 1
        if correct:
            self.correct_streak += 1
            self.correct_count += 1
        else:
            self.correct_streak = 0
            self.this_hand_clean = False
            self.clean_hands = 0  # any wrong answer also resets clean-hand streak

    def frequency(self) -> float:
        """Probability the modal fires on the next meaningful decision."""
        s = self.correct_streak
        if s >= 15: return 0.20
        if s >= 10: return 0.33
        if s >= 5:  return 0.66
        return 1.0

    def ready_to_graduate(self) -> bool:
        """True when the user has earned the option to advance."""
        # Need at least 10 quizzes attempted, current accuracy is high,
        # and we're at low frequency (= they're consistent enough to be on cruise).
        return (
            self.quiz_count >= 10
            and self.clean_hands >= 5
            and self.frequency() <= 0.33
        )


def get_stage(stage_id: int) -> TrainingStage:
    for s in STAGES:
        if s.id == stage_id:
            return s
    return STAGES[0]


def next_stage(stage_id: int) -> TrainingStage | None:
    for i, s in enumerate(STAGES):
        if s.id == stage_id and i + 1 < len(STAGES):
            return STAGES[i + 1]
    return None


def is_meaningful_decision(ctx: dict[str, Any]) -> bool:
    """Skip the modal for trivial spots (auto-fold 72o pre-flop, etc.) where
    the quiz would just be noise. A spot is 'meaningful' if there's a bet to
    face OR the user has outs OR a value-betting spot exists.
    """
    if ctx.get("to_call", 0) > 0:
        return True
    if ctx.get("outs", 0) > 0:
        return True
    spot = ctx.get("spot", "")
    if spot in ("value_bet", "value_raise", "pure_bluff", "semi_bluff"):
        return True
    return False


def stage_is_relevant(stage_id: int, ctx: dict[str, Any]) -> bool:
    """Per-stage relevance gate: the stage's questions need to make sense in
    this specific decision context. Don't ask 'count your outs' pre-flop where
    no board exists. Don't ask 'pot odds' when there's no bet to face.
    """
    street = ctx.get("street", "preflop")
    to_call = ctx.get("to_call", 0)
    board_cards = len(ctx.get("board") or [])
    outs = ctx.get("outs", 0)
    has_bet = to_call > 0

    if stage_id == 1:
        # Hand reading: any street is fine. Pre-flop, the "hand class" is just
        # "Pair" or "High card" which is still a meaningful read.
        return True
    if stage_id == 2:
        # Outs counting: requires a board AND actual outs to count
        return board_cards >= 3 and outs > 0
    if stage_id == 3:
        # Pot odds: requires a bet to face
        return has_bet
    if stage_id == 4:
        # EV: meaningful whenever there's a non-trivial decision
        return has_bet or outs > 0
    if stage_id == 5:
        # Position: meaningful any time hero has a clear position label
        return ctx.get("hero_position") is not None
    if stage_id == 6:
        # Ranges: need an opponent who has taken meaningful action
        return ctx.get("villain_archetype") is not None
    if stage_id == 7:
        # Archetypes: need an identifiable archetype
        return ctx.get("villain_archetype") is not None
    if stage_id == 8:
        # Exploits: any spot with a known opponent style
        return ctx.get("villain_archetype") is not None
    return True


def should_quiz(state: StageState, ctx: dict[str, Any], rng: random.Random) -> bool:
    """Should we fire the modal on this decision?"""
    if not is_meaningful_decision(ctx):
        return False
    if not stage_is_relevant(state.stage_id, ctx):
        return False
    return rng.random() < state.frequency()


# ---------------------------------------------------------------------------
# Build questions for a specific decision context
# ---------------------------------------------------------------------------

def build_quiz_for_decision(
    state: StageState,
    ctx: dict[str, Any],
) -> dict[str, Any]:
    """Construct the quiz payload sent to the client.

    Returns a dict with: stage_id, stage_title, questions[], handled_summary{}.
    Each question includes prompt, choices/correct_answer for grading later.
    """
    stage = get_stage(state.stage_id)
    questions = []
    for sq in stage.questions:
        questions.append(_render_question(sq, ctx))
    return {
        "stage_id": stage.id,
        "stage_title": stage.title,
        "stage_teaches": stage.teaches,
        "questions": questions,
        "handled_for_you": stage.handled_for_you,
        "handled_summary": _handled_summary(stage, ctx),
        "frequency": state.frequency(),
        "correct_streak": state.correct_streak,
        "clean_hands": state.clean_hands,
        "ready_to_graduate": state.ready_to_graduate(),
    }


def detailed_feedback(question: dict[str, Any], submitted: Any, ctx: dict[str, Any]) -> str:
    """Generate concept-aware feedback for a wrong answer.

    For outs: show the actual breakdown (9 flush + 3 straight = 12) so the
    student sees exactly which outs they missed.

    For pot odds: show the formula expanded with both their number AND the
    correct number plugged in.

    For EV: same — show the formula expansion with their inputs.
    """
    qid = question.get("id", "")
    correct = question.get("correct")

    if qid == "outs":
        breakdown = ctx.get("outs_breakdown") or {}
        cats = breakdown.get("categories") or []
        if not cats:
            return f"There are no clean outs in this spot."
        try:
            submitted_n = int(submitted)
        except (TypeError, ValueError):
            submitted_n = None
        lines: list[str] = []
        for cat in cats:
            cards_pretty = " ".join(cat["cards"][:12])
            extra = "" if len(cat["cards"]) <= 12 else f" (+{len(cat['cards'])-12} more)"
            lines.append(f"<strong>{cat['count']} → {cat['name']}</strong>: {cards_pretty}{extra}")
        total = breakdown.get("total", 0)
        if submitted_n is None:
            preamble = f"There are <strong>{total}</strong> clean outs total."
        elif abs(submitted_n - total) <= 1:
            preamble = f"Close — you said {submitted_n}, the answer is {total}."
        elif submitted_n > total:
            preamble = (f"You said {submitted_n} — that's too many, probably double-counting. "
                        f"There are {total} clean outs:")
        else:
            preamble = (f"You said {submitted_n} — missed some. There are {total} clean outs:")
        return f"{preamble}<br>" + "<br>".join(lines)

    if qid == "pot_odds":
        to_call = int(ctx.get("to_call", 0))
        pot = int(ctx.get("pot", 0))
        pot_before_bet = pot - to_call
        po = float(correct or 0)
        return (
            f"Formula: <code>to_call ÷ (pot + to_call)</code>.<br>"
            f"Pot was <strong>{pot_before_bet}</strong> before the bet. Opponent bet <strong>{to_call}</strong>, "
            f"so the pot is now <strong>{pot}</strong>. You'd add <strong>{to_call}</strong> to win <strong>{pot}</strong>.<br>"
            f"{to_call} ÷ ({pot} + {to_call}) = {to_call} ÷ {pot + to_call} = "
            f"<strong>{po:.1f}%</strong> needed to break even."
        )

    if qid == "ev_call":
        eq = float(ctx.get("equity_pct", 0)) / 100.0
        to_call = int(ctx.get("to_call", 0))
        pot = int(ctx.get("pot", 0))
        ev_correct = float(correct or 0)
        return (
            f"Formula: <code>EV = equity × pot − (1 − equity) × to_call</code>.<br>"
            f"Your equity is {eq*100:.0f}%, pot is {pot}, call is {to_call}.<br>"
            f"({eq:.2f} × {pot}) − ({1-eq:.2f} × {to_call}) = "
            f"{eq*pot:.1f} − {(1-eq)*to_call:.1f} = "
            f"<strong>{ev_correct:+.1f} chips</strong> per call on average."
        )

    if qid == "should_call":
        eq = float(ctx.get("equity_pct", 0))
        po = float(ctx.get("pot_odds_required_pct", 0))
        if eq >= po:
            return (f"Your equity ({eq:.0f}%) is above the {po:.0f}% you need. "
                    f"Edge = +{eq-po:.0f}% → continuing makes money in the long run.")
        return (f"Your equity ({eq:.0f}%) is below the {po:.0f}% you need. "
                f"Edge = −{po-eq:.0f}% → folding is the correct play.")

    if qid == "hand_class":
        right = question.get("correct", "")
        return f"Your best 5-card hand right now is a <strong>{right}</strong>."

    if qid == "best_action":
        labels = ctx.get("ev_labels_ranked") or []
        if not labels:
            return ""
        return (
            f"The EV-max action is <strong>{labels[0]}</strong>. "
            f"The other options ranked: {' · '.join(labels[1:4])}."
        )

    # Generic fallback
    return question.get("formula") or question.get("hint", "")


def _render_question(sq: StageQuestion, ctx: dict[str, Any]) -> dict[str, Any]:
    """Generate the question's choices and the canonical correct answer for grading."""
    q: dict[str, Any] = {
        "id": sq.id,
        "prompt": sq.prompt,
        "answer_type": sq.answer_type,
        "hint": sq.hint,
        "formula": sq.formula,
        "choices": [],
        "correct": None,
        "tolerance": 0,
    }
    if sq.id == "hand_class":
        # 9 hand classes (excluding royal flush since it'd be obvious)
        classes = [
            "High card", "Pair", "Two pair", "Three of a kind",
            "Straight", "Flush", "Full house", "Four of a kind", "Straight flush",
        ]
        q["choices"] = classes
        # Server fills correct based on actual hand evaluation in session.py
        q["correct"] = ctx.get("hand_class_label", "Pair")
    elif sq.id == "outs":
        out_count = int(ctx.get("outs", 0))
        q["correct"] = out_count
        q["tolerance"] = 1
    elif sq.id == "pot_odds":
        po = float(ctx.get("pot_odds_required_pct", 0))
        q["correct"] = round(po, 1)
        q["tolerance"] = 3.0
    elif sq.id == "should_call":
        edge = float(ctx.get("edge", 0))
        q["choices"] = ["Continue (call or raise) — equity beats the price",
                         "Fold — equity below the price"]
        q["correct"] = 0 if edge > 0 else 1
    elif sq.id == "ev_call":
        ev = float(ctx.get("ev_call", 0))
        q["correct"] = round(ev, 1)
        q["tolerance"] = 2.0
    elif sq.id == "best_action":
        labels = ctx.get("ev_labels_ranked") or []
        # Strip the EV number to keep choices clean
        q["choices"] = [l.split(" (")[0] for l in labels[:4]] or ["Fold", "Call", "Raise"]
        q["correct"] = 0  # the highest-EV option is always sorted first
    elif sq.id == "position_aware":
        ip = bool(ctx.get("in_position", False))
        q["choices"] = [
            "In position — I can defend WIDER",
            "Out of position — I should defend TIGHTER",
            "Position doesn't matter here",
        ]
        q["correct"] = 0 if ip else 1
    elif sq.id == "villain_range":
        # Build a 4-choice MC from the actual range size
        rs = int(ctx.get("villain_range_size", 200))
        # Total combos = 1326; convert to %
        pct = rs / 1326 * 100
        buckets = [
            ("top 10% (premium pairs, AK)", 10),
            ("top 20% (pairs + broadway)", 20),
            ("top 35% (broad opening)", 35),
            ("top 50%+ (very wide / station)", 60),
        ]
        # Pick the closest bucket as correct
        best = min(buckets, key=lambda b: abs(b[1] - pct))
        q["choices"] = [b[0] for b in buckets]
        q["correct"] = [b[0] for b in buckets].index(best[0])
    elif sq.id == "archetype_id":
        actual = ctx.get("villain_archetype") or "TAG"
        all_arches = ["Nit", "TAG", "LAG", "Calling Station", "Maniac", "Whale", "GTO Reg"]
        # 4 choices including the right one
        import random as _r
        rng = _r.Random(hash(actual) & 0xFFFF)
        distractors = [a for a in all_arches if a != actual]
        rng.shuffle(distractors)
        choices = distractors[:3] + [actual]
        rng.shuffle(choices)
        q["choices"] = choices
        q["correct"] = choices.index(actual)
    elif sq.id == "exploit":
        # Derive the right exploit from villain archetype
        exploits = {
            "Nit": "Steal more — they fold to aggression",
            "Calling Station": "Value-bet thin, never bluff",
            "Whale": "Value-bet wide, never bluff",
            "TAG": "Balanced — bluff selectively in position",
            "LAG": "Widen calling range, tighten own bluffs",
            "Maniac": "Tighten up + call lighter, don't bluff back",
            "GTO Reg": "Stay close to GTO — small exploits only",
        }
        actual = ctx.get("villain_archetype") or "TAG"
        correct_text = exploits.get(actual, "Balanced play")
        all_exploits = list(set(exploits.values()))
        import random as _r
        rng = _r.Random(hash(actual) & 0xFFFF)
        distractors = [e for e in all_exploits if e != correct_text]
        rng.shuffle(distractors)
        choices = distractors[:3] + [correct_text]
        rng.shuffle(choices)
        q["choices"] = choices
        q["correct"] = choices.index(correct_text)
    return q


def _handled_summary(stage: TrainingStage, ctx: dict[str, Any]) -> dict[str, str]:
    """Friendly preview of what the app is computing FOR the user this stage."""
    out: dict[str, str] = {}
    if "equity" in stage.handled_for_you:
        out["Equity"] = f"{ctx.get('equity_pct', 0):.0f}% to win"
    if "EV" in stage.handled_for_you:
        ev_labels = ctx.get("ev_labels_ranked") or []
        if ev_labels:
            out["EV per action"] = " · ".join(ev_labels[:3])
    if "position" in stage.handled_for_you:
        pos = ctx.get("hero_position") or "—"
        ip = "in position" if ctx.get("in_position") else "out of position"
        out["Position"] = f"{pos} ({ip})"
    if "range" in stage.handled_for_you:
        rs = ctx.get("villain_range_size") or 0
        out["Villain range"] = f"~{rs} combos"
    if "archetype" in stage.handled_for_you:
        out["Opponent"] = ctx.get("villain_archetype") or "unknown"
    if "exploit" in stage.handled_for_you:
        out["Recommended"] = ctx.get("verdict_label") or "—"
    return out


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

def grade_answer(question: dict[str, Any], submitted: Any) -> bool:
    """Check whether a submitted answer is correct."""
    if question["answer_type"] == "mc":
        try:
            if isinstance(question["correct"], int):
                return int(submitted) == question["correct"]
            return submitted == question["correct"]
        except Exception:
            return False
    if question["answer_type"] == "numeric":
        try:
            return abs(float(submitted) - float(question["correct"])) <= float(question["tolerance"])
        except Exception:
            return False
    return False
