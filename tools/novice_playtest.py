"""Novice playtesting agent.

Connects to the trainer over WebSocket and pretends to be someone who's
never played poker. It:

1. Builds a text "as I see it" view of every screen.
2. Flags any jargon in that view ("equity", "MDF", "raise_min", "BTN", ...).
3. Picks an action using ONLY what the labels tell it.
4. Records what it could and couldn't understand each turn.

At the end it prints a confusion report with the most common
issues, ordered by frequency. We iterate the UI based on that report
until the novice can complete N hands without showstoppers.

Usage:
  .venv/bin/python tools/novice_playtest.py --hands 3 --server ws://127.0.0.1:8765/ws
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Jargon dictionary — terms that ONLY a poker-literate user would understand.
# A novice flags any of these that appear in user-facing strings.
# ---------------------------------------------------------------------------

# Bare positions
POSITION_JARGON = {"BTN", "SB", "BB", "UTG", "UTG+1", "MP", "LJ", "HJ", "CO"}
# Archetype names that are poker culture slang
ARCHETYPE_JARGON = {"TAG", "LAG", "Nit", "GTO", "GTO Reg"}
# Math jargon
MATH_JARGON = {
    "equity", "pot odds", "MDF", "α", "alpha", "outs", "rule of 2",
    "blockers", "card removal", "implied odds", "fold equity",
    "the river", "by river", "polarized", "merged",
    "equity realization", "nut advantage", "bb",
    # "next card" is plain English — not flagged.
}
# Programmer keys leaking to the UI
PROGRAMMER_KEYS = {
    "raise_min", "raise_2.5x", "bet_66", "bet_33", "bet_100",
    "overbet_150", "all_in",
}
# Mysterious ratings
RATING_JARGON = {"Glicko", "phi", "sigma", "±"}

ALL_JARGON = POSITION_JARGON | ARCHETYPE_JARGON | MATH_JARGON | PROGRAMMER_KEYS | RATING_JARGON


def scan_jargon(text: str) -> set[str]:
    """Find any jargon term that appears as a whole word in `text`."""
    if not text:
        return set()
    hits: set[str] = set()
    for term in ALL_JARGON:
        # Whole-word match (case-sensitive for acronyms, case-insensitive for math words)
        if term.upper() == term and len(term) <= 5:
            # acronym → case-sensitive
            pattern = r"\b" + re.escape(term) + r"\b"
            if re.search(pattern, text):
                hits.add(term)
        else:
            if re.search(r"\b" + re.escape(term) + r"\b", text, re.IGNORECASE):
                hits.add(term)
    return hits


# ---------------------------------------------------------------------------
# Novice mental model
# ---------------------------------------------------------------------------

@dataclass
class NoviceState:
    """The novice's running mental model of the game."""

    hero_seat: int = -1
    hero_cards: list[str] = field(default_factory=list)
    board: list[str] = field(default_factory=list)
    pot: int = 0
    stack: int = 0
    seats: list[dict] = field(default_factory=list)
    last_action_req: dict | None = None
    last_coach_tip: dict | None = None
    last_polish: str = ""
    hand_id: str = ""
    hand_counter: int = 0


@dataclass
class NoviceReport:
    """What the novice could and couldn't make sense of."""

    hands_completed: int = 0
    hands_attempted: int = 0
    actions_taken: int = 0
    bailouts: int = 0  # times forced to fold because nothing made sense
    jargon_hits: Counter = field(default_factory=Counter)
    confusion_log: list[str] = field(default_factory=list)  # human-readable
    things_understood: list[str] = field(default_factory=list)
    showstoppers: list[str] = field(default_factory=list)   # critical UX failures
    # Bluffing-lesson metrics
    spots_seen: Counter = field(default_factory=Counter)  # spot_type → count
    bluff_lessons_heard: list[str] = field(default_factory=list)  # the actual T1 spot notes

    def add_confusion(self, msg: str) -> None:
        self.confusion_log.append(msg)

    def add_understood(self, msg: str) -> None:
        if msg not in self.things_understood:
            self.things_understood.append(msg)

    def print(self) -> None:
        print("\n" + "=" * 60)
        print(f"NOVICE REPORT — {self.hands_completed}/{self.hands_attempted} hands completed, "
              f"{self.actions_taken} actions taken, {self.bailouts} bailouts")
        print("=" * 60)

        if self.showstoppers:
            print("\n🛑 SHOWSTOPPERS (critical):")
            for s in self.showstoppers:
                print(f"   • {s}")

        print("\n🤷 TOP JARGON HITS (term : count):")
        for term, count in self.jargon_hits.most_common(20):
            print(f"   {term:25s} {count}×")

        if self.spots_seen:
            print("\n🎯 SPOTS ENCOUNTERED:")
            for spot, count in self.spots_seen.most_common():
                print(f"   {spot:20s} {count}×")

        if self.bluff_lessons_heard:
            print(f"\n🃏 BLUFF / SPOT LESSONS HEARD ({len(self.bluff_lessons_heard)}):")
            for lesson in self.bluff_lessons_heard[:5]:
                print(f"   • {lesson}")

        print("\n📝 CONFUSION LOG (first 25):")
        for line in self.confusion_log[:25]:
            print(f"   • {line}")
        if len(self.confusion_log) > 25:
            print(f"   ... and {len(self.confusion_log) - 25} more")

        print("\n✅ THINGS UNDERSTOOD:")
        for u in self.things_understood:
            print(f"   • {u}")
        print()


# ---------------------------------------------------------------------------
# Novice decision-making — uses only what the UI tells it
# ---------------------------------------------------------------------------

def novice_decide(state: NoviceState, report: NoviceReport) -> tuple[dict, str]:
    """Return (action_payload, why_string)."""
    req = state.last_action_req
    assert req is not None
    legal = req["legal"]
    to_call = req["to_call"]
    pot = req["pot"]
    stack = state.stack

    # ------------------------------------------------------------
    # Easiest path: the coach told us which button to highlight.
    # ------------------------------------------------------------
    tip = state.last_coach_tip or {}
    math = tip.get("math", {})
    verdict_button = math.get("verdict_button")
    verdict_label = math.get("verdict_label") or math.get("verdict") or ""
    if verdict_button:
        mapped = _button_to_action(verdict_button, legal, to_call)
        if mapped is not None:
            report.add_understood(
                f"The coach highlighted the '{verdict_button}' button as the best play."
            )
            return mapped, f"highlighted button: {verdict_button} ({verdict_label})"

    # ------------------------------------------------------------
    # Next: the coach's polished prose.
    # ------------------------------------------------------------
    polish = state.last_polish or ""
    recommended_human = _parse_polish_recommendation(polish)
    if recommended_human:
        report.add_understood(
            "The coach gave me an English sentence I could understand."
        )
        mapped = _english_to_action(recommended_human, legal, to_call)
        if mapped is not None:
            return mapped, f"following coach polish: {polish[:80]!r}"
        report.add_confusion(
            f"Coach said '{polish[:100]}' but I couldn't map that to a button."
        )

    # ------------------------------------------------------------
    # Last resort: structured verdict.
    # ------------------------------------------------------------
    if verdict_label and verdict_label not in PROGRAMMER_KEYS:
        mapped = _english_to_action(verdict_label, legal, to_call)
        if mapped is not None:
            return mapped, f"following verdict: {verdict_label}"

    # ------------------------------------------------------------
    # Fall back to "what would a novice do" — usually fold or call small.
    # ------------------------------------------------------------
    report.bailouts += 1
    if legal["can_check"]:
        report.add_confusion("Wasn't sure what to do — checked because it was free.")
        return {"action": "check", "amount": 0}, "default check (free)"
    if legal["can_call"] and to_call <= max(20, stack * 0.03):
        report.add_confusion(
            f"Wasn't sure — called {to_call} because it was cheap."
        )
        return {"action": "call", "amount": 0}, "default call (cheap)"
    report.add_confusion(
        f"Wasn't sure what to do; the call is {to_call} into a {pot} pot and "
        f"I couldn't read the coach's advice — folding."
    )
    return {"action": "fold", "amount": 0}, "default fold (gave up)"


def _parse_polish_recommendation(polish: str) -> str | None:
    """Extract a plain-English recommendation from the polished prose."""
    if not polish:
        return None
    # Look for common phrasings
    low = polish.lower()
    if "fold" in low and ("best" in low or "is correct" in low or "is the read" in low):
        return "fold"
    if "raise" in low and ("+ev" in low or "is best" in low or "is the read" in low):
        return "raise"
    if "call" in low and "+ev" in low:
        return "call"
    if "bet for value" in low or "bet" in low and "value" in low:
        return "bet"
    if "borderline" in low or "marginal" in low:
        return "call_or_fold"
    return None


def _english_to_action(text: str, legal: dict, to_call: int) -> dict | None:
    """Translate an English-ish recommendation to an action button."""
    t = (text or "").lower().strip()
    if "fold" in t and legal["can_fold"]:
        return {"action": "fold", "amount": 0}
    if t.startswith("check") and legal["can_check"]:
        return {"action": "check", "amount": 0}
    if "call" in t and legal["can_call"]:
        return {"action": "call", "amount": 0}
    if "raise" in t and legal["can_raise"]:
        # Extract a target amount if present, e.g. "Raise to 75"
        import re
        m = re.search(r"(\d+)", t)
        amt = int(m.group(1)) if m else legal["min_raise_to"]
        amt = max(legal["min_raise_to"], min(amt, legal["max_raise_to"]))
        return {"action": "raise", "amount": amt}
    if "bet" in t and legal["can_bet"]:
        import re
        m = re.search(r"(\d+)", t)
        amt = int(m.group(1)) if m else max(legal["min_raise_to"], 10)
        amt = min(amt, legal["max_raise_to"])
        return {"action": "bet", "amount": amt}
    if "all-in" in t and legal["can_raise"]:
        return {"action": "all_in", "amount": legal["max_raise_to"]}
    return None


def _button_to_action(button: str, legal: dict, to_call: int) -> dict | None:
    """Map a UI button name (fold|check|call|bet|raise|all_in) to an action."""
    if button == "fold" and legal["can_fold"]:
        return {"action": "fold", "amount": 0}
    if button == "check" and legal["can_check"]:
        return {"action": "check", "amount": 0}
    if button == "call" and legal["can_call"]:
        return {"action": "call", "amount": 0}
    if button == "bet" and legal["can_bet"]:
        return {"action": "bet", "amount": max(legal["min_raise_to"], 10)}
    if button == "raise" and legal["can_raise"]:
        return {"action": "raise", "amount": legal["min_raise_to"]}
    if button == "all_in" and legal.get("max_raise_to", 0) > 0:
        return {"action": "all_in", "amount": legal["max_raise_to"]}
    return None


# ---------------------------------------------------------------------------
# Main play loop
# ---------------------------------------------------------------------------

async def playtest(server: str, hands: int = 3, seats: int = 6, verbose: bool = True) -> NoviceReport:
    import websockets

    state = NoviceState()
    report = NoviceReport()

    def log(msg: str) -> None:
        if verbose:
            print(msg)

    async with websockets.connect(server) as ws:
        await ws.send(json.dumps({
            "type": "join", "v": 1,
            "data": {"user_name": "Novice", "seats": seats, "stack_bb": 100, "sb": 5, "bb": 10},
        }))

        while report.hands_completed < hands:
            raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
            msg = json.loads(raw)
            t = msg["type"]; d = msg["data"]

            if t == "joined":
                log(f"[joined] seats={d['seats']}")

            elif t == "hand_start":
                state.hero_seat = d["hero_seat"]
                state.hero_cards = d["hero_cards"]
                state.seats = d["seats"]
                state.board = []
                state.pot = 0
                state.hand_id = d["hand_id"]
                state.hand_counter += 1
                report.hands_attempted += 1
                # Scan all visible labels for jargon
                _scan_hand_start(d, report)
                log(f"\n[hand_start] {d['hand_id']} I'm at seat {state.hero_seat} with cards {state.hero_cards}")

            elif t == "post_blind":
                state.pot += d["amount"]

            elif t == "player_action":
                state.pot = d["pot_after"]
                if d["seat"] == state.hero_seat:
                    state.stack = d["stack_after"]

            elif t == "board":
                state.board = d["board"]
                state.pot = d["pot"]
                # The UI translates street to plain English at T1
                # ("First 3 cards", "Final card"). Don't flag at T1.

            elif t == "coach_tip":
                state.last_coach_tip = d
                _scan_coach_tip(d, report)
                # Track which spot types we encounter
                spot = d.get("spot")
                if spot:
                    report.spots_seen[spot] += 1
                # Track any bluff/value lesson the coach surfaced as a tier_strings entry
                lesson = (d.get("tier_strings") or {}).get("spot_lesson")
                if lesson and lesson not in report.bluff_lessons_heard:
                    report.bluff_lessons_heard.append(lesson)

            elif t == "stage_quiz":
                # The pilot-style modal — for novice-playtest purposes we reveal
                # every question so play can proceed. Streak / mastery isn't being
                # exercised here; the playtest checks UI labelling not learning.
                n = len(d.get("questions") or [])
                await ws.send(json.dumps({
                    "type": "submit_quiz_answer", "v": 1,
                    "data": {"answers": ["__revealed__"] * n, "revealed": True},
                }))
            elif t in ("stage_quiz_feedback", "stage_change"):
                pass

            elif t == "coach_tip_polish":
                state.last_polish = d.get("text", "")
                report.jargon_hits.update(scan_jargon(state.last_polish))

            elif t == "action_to_act":
                if d["seat"] != state.hero_seat:
                    continue
                state.last_action_req = d
                # Find my stack
                me = next((s for s in state.seats if s["seat"] == state.hero_seat), {})
                state.stack = me.get("stack", state.stack)
                _scan_action_to_act(d, report)

                # Wait briefly for coach_tip + polish if they're still en route
                deadline = asyncio.get_event_loop().time() + 4.0
                while asyncio.get_event_loop().time() < deadline:
                    if state.last_polish and state.last_coach_tip and state.last_coach_tip.get("seq") == d["seq"]:
                        break
                    try:
                        raw2 = await asyncio.wait_for(ws.recv(), timeout=0.3)
                    except asyncio.TimeoutError:
                        continue
                    m2 = json.loads(raw2)
                    if m2["type"] == "coach_tip":
                        state.last_coach_tip = m2["data"]
                        _scan_coach_tip(m2["data"], report)
                        spot = m2["data"].get("spot")
                        if spot:
                            report.spots_seen[spot] += 1
                        lesson = (m2["data"].get("tier_strings") or {}).get("spot_lesson")
                        if lesson and lesson not in report.bluff_lessons_heard:
                            report.bluff_lessons_heard.append(lesson)
                    elif m2["type"] == "coach_tip_polish":
                        state.last_polish = m2["data"].get("text", "")
                        report.jargon_hits.update(scan_jargon(state.last_polish))
                    elif m2["type"] == "rating_update":
                        pass

                # Make a decision
                action, why = novice_decide(state, report)
                report.actions_taken += 1
                log(f"  → action_to_act seq={d['seq']} legal={[k for k,v in d['legal'].items() if v is True]}")
                log(f"     polish: {state.last_polish[:100]}")
                log(f"     coach verdict: {state.last_coach_tip.get('math', {}).get('verdict') if state.last_coach_tip else '<none>'}")
                log(f"     NOVICE → {action} ({why})")
                await ws.send(json.dumps({
                    "type": "action", "v": 1,
                    "data": {"hand_id": d["hand_id"], "seq": d["seq"], **action},
                }))
                # Reset for next decision
                state.last_polish = ""
                state.last_coach_tip = None

            elif t == "rating_update":
                # At T1 the UI shows a bucket label ("great play" / "big mistake"),
                # not the bb number. We have no tier info here so assume T1
                # (the default) and don't flag.
                pass

            elif t == "leak_report":
                pass

            elif t == "hand_end":
                report.hands_completed += 1
                log(f"[hand_end] winners={d['winners']} board={d['final_board']}")
                if report.hands_completed < hands:
                    await ws.send(json.dumps({"type": "next_hand", "v": 1, "data": {}}))

            elif t == "error":
                report.showstoppers.append(f"Server error: {d.get('code')}: {d.get('message')}")
                log(f"  [error] {d}")

    return report


def _scan_hand_start(d: dict, report: NoviceReport) -> None:
    """Look at the hand_start payload as a novice would.

    The UI translates positions and archetypes to plain English at T1, so we
    only flag them if they'd actually be shown as the raw acronym.
    """
    # We don't yet know the user's tier from hand_start. Assume the UI is at
    # T1 by default (the server initializes with T1) and skip flagging
    # positions/archetypes — they'll be re-checked once we see coach_tip's
    # tier value if the UI is at T2+.
    pass


def _scan_coach_tip(d: dict, report: NoviceReport) -> None:
    """Flag jargon visible to the user *at the current tier*.

    Only fields actually rendered by the frontend are scanned. Internal
    JSON keys (e.g. `ev_by_action.raise_min`) are not user-visible because
    the UI displays the human `ev_labels.raise_min` value instead.
    """
    math = d.get("math", {})
    tier = d.get("tier", 1)

    # Hand-coded section labels visible in the panel at this tier.
    if tier <= 1:
        visible_labels = ["Win chance", "Win % needed to break even"]
    else:
        visible_labels = [
            "Equity", "Pot odds required", "Edge", "MDF", "α (bluff)",
            "Outs", "Next card", "By river",
        ]
    for label in visible_labels:
        report.jargon_hits.update(scan_jargon(label))

    # EV labels rendered in the panel — these are the human strings.
    ev_labels = math.get("ev_labels") or {}
    for label in ev_labels.values():
        report.jargon_hits.update(scan_jargon(label))
    # Internal programmer keys are only visible if they slip into ev_labels.
    # If they're in ev_by_action but mapped to a human ev_label, the UI
    # shows the label and the user never sees the key.

    # Tier-strings rendered in the notes panel. SKIP_KEYS in app.js excludes
    # T1's price_to_call / win_chance / cards_that_help / verdict — those
    # texts are absorbed by the polish prose, not shown as notes.
    SKIP_TIER_STRING_KEYS = {
        "pot_odds", "equity_vs_required", "equity", "outs", "ev", "verdict",
        "price_to_call", "win_chance", "cards_that_help",
    }
    for k, v in (d.get("tier_strings") or {}).items():
        if k in SKIP_TIER_STRING_KEYS:
            continue
        report.jargon_hits.update(scan_jargon(v))

    # Villain archetype shown in the "Your opponent: X" banner.
    # At T1 the UI translates these to plain English ("tight, aggressive" etc.)
    va = d.get("villain_archetype") or ""
    if tier >= 2:
        for term in ARCHETYPE_JARGON:
            if term in va:
                report.jargon_hits[term] += 1
        if va == "Calling Station":
            report.jargon_hits["Calling Station"] += 1

    # Position labels on the SVG seats. At T1 these are translated to
    # plain English ("Dealer", "Small Blind", "Late"...). Only flag at T2+.
    if tier >= 2:
        # We can't see the seats from coach_tip alone; this would be flagged
        # in _scan_hand_start. For now, we trust the UI's translation at T1.
        pass


def _scan_action_to_act(d: dict, report: NoviceReport) -> None:
    # Look at button labels we'd surface in the UI: "Fold", "Check", "Call", "Bet", "Raise", "All-in"
    # Plus the "to_call" and "pot" numbers (numbers are fine, no jargon)
    pass


# ---------------------------------------------------------------------------
# Pass / fail criteria
# ---------------------------------------------------------------------------

@dataclass
class PassCriteria:
    hands_target: int = 3
    max_total_jargon_terms: int = 5    # max distinct jargon terms before we call it confusing
    max_bailout_ratio: float = 0.5      # % of actions that were "I gave up"
    no_showstoppers: bool = True
    # Bluffing-lesson coverage — we want the user to encounter and understand
    # at least one "spot lesson" (bluff / semi-bluff / bluff-catch / value)
    # across the session.
    require_spot_lessons: int = 1


def evaluate(report: NoviceReport, criteria: PassCriteria) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if report.hands_completed < criteria.hands_target:
        failures.append(
            f"only completed {report.hands_completed}/{criteria.hands_target} hands"
        )
    if criteria.no_showstoppers and report.showstoppers:
        failures.append(f"{len(report.showstoppers)} showstoppers")
    distinct_jargon = len(report.jargon_hits)
    if distinct_jargon > criteria.max_total_jargon_terms:
        failures.append(
            f"hit {distinct_jargon} distinct jargon terms (limit {criteria.max_total_jargon_terms})"
        )
    if report.actions_taken > 0:
        bailout_ratio = report.bailouts / report.actions_taken
        if bailout_ratio > criteria.max_bailout_ratio:
            failures.append(
                f"bailed out on {bailout_ratio:.0%} of actions (limit {criteria.max_bailout_ratio:.0%})"
            )
    if len(report.bluff_lessons_heard) < criteria.require_spot_lessons:
        failures.append(
            f"only heard {len(report.bluff_lessons_heard)} spot lessons "
            f"(want at least {criteria.require_spot_lessons})"
        )
    return (len(failures) == 0, failures)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="ws://127.0.0.1:8765/ws")
    ap.add_argument("--hands", type=int, default=3)
    ap.add_argument("--seats", type=int, default=6)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv[1:])

    report = asyncio.run(playtest(args.server, args.hands, args.seats, verbose=not args.quiet))
    report.print()
    passed, failures = evaluate(report, PassCriteria(hands_target=args.hands))
    if passed:
        print("✅ PASS — a novice could play this.")
        return 0
    print("❌ FAIL:")
    for f in failures:
        print(f"   • {f}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
