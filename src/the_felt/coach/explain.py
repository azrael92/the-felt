"""Tiered explanation strings (deterministic, no LLM).

Tier 1 (Beginner): pot odds, outs (rule of 2/4), required equity, verdict.
Tier 2 (Intermediate): adds hand-vs-range equity, blockers, position note.
Tier 3 (Advanced): adds MDF/alpha, polarized vs merged framing, equity realization.
Tier 4 (Expert): adds mixed-strategy frequencies, exploit deviations.

Each renderer returns a dict[str, str] with named components so the UI can
display them as a structured panel rather than a wall of text.
"""

from __future__ import annotations

from the_felt.coach.analyzer import DecisionContext


def _spot_emoji(spot: str) -> str:
    """Tiny visual cue for the spot type."""
    return {
        "value_raise":  "💰",
        "value_bet":    "💰",
        "value_call":   "✓",
        "bluff_catch":  "🎯",
        "semi_bluff":   "🌀",
        "pure_bluff":   "🃏",
        "marginal":     "⚖",
        "give_up":      "✋",
    }.get(spot, "")


def render_tier(ctx: DecisionContext, tier: int) -> dict[str, str]:
    """Produce a dict of named explanation strings appropriate for `tier`.

    Tier 1 (Beginner) uses plain English: "win chance", "price to call",
    no jargon. Higher tiers introduce the real terms (equity, pot odds, MDF).
    """
    tier = max(1, min(4, tier))
    out: dict[str, str] = {}

    if tier == 1:
        # Plain-English beginner mode.
        if ctx.to_call > 0:
            out["price_to_call"] = (
                f"You'd pay {ctx.to_call} to stay in the {ctx.pot}-chip pot — "
                f"so you'd need to win at least {ctx.pot_odds_required*100:.0f}% of the time to break even."
            )
            out["win_chance"] = (
                f"Your hand should win about {ctx.equity*100:.0f}% of the time "
                f"against what your opponent could be holding."
            )
            out["verdict"] = (
                f"That's {'better' if ctx.edge > 0 else 'worse'} than break-even, "
                f"so the best play here is: **{ctx.verdict_label or ctx.verdict}**."
            )
        else:
            out["win_chance"] = (
                f"Your hand should win about {ctx.equity*100:.0f}% of the time "
                f"against your opponent's likely holdings."
            )
            out["verdict"] = f"Best play: **{ctx.verdict_label or ctx.verdict}**."
        if ctx.outs > 0:
            out["cards_that_help"] = (
                f"There are {ctx.outs} cards left in the deck that improve your hand — "
                f"about {ctx.rule_2_4[1]*100:.0f}% chance to hit one by the final card."
            )
        # Bluff / spot lesson (T1 plain-English)
        if ctx.spot_t1_note:
            out["spot_lesson"] = _spot_emoji(ctx.spot) + " " + ctx.spot_t1_note
        return out

    # ---- Tier 2+: introduce the real terms + bluff/spot notes ----
    if ctx.spot_t2_note and tier >= 2:
        out["spot_lesson"] = _spot_emoji(ctx.spot) + " " + ctx.spot_t2_note
    if ctx.spot_t3_note and tier >= 3:
        out["spot_polarity"] = ctx.spot_t3_note
    if ctx.spot_t4_note and tier >= 4:
        out["spot_exploit"] = ctx.spot_t4_note

    if ctx.to_call > 0:
        out["pot_odds"] = (
            f"You need {ctx.pot_odds_required*100:.1f}% equity to break even on this call "
            f"({ctx.to_call} into a pot of {ctx.pot - ctx.to_call} = "
            f"{ctx.to_call}/{ctx.pot} pot odds)."
        )
        out["equity_vs_required"] = (
            f"Your equity is {ctx.equity*100:.1f}%, "
            f"{'above' if ctx.edge > 0 else 'below'} the {ctx.pot_odds_required*100:.1f}% needed "
            f"(edge {ctx.edge*100:+.1f}%)."
        )
    else:
        out["equity"] = f"You hold {ctx.equity*100:.1f}% equity."

    if ctx.outs > 0:
        out["outs"] = (
            f"{ctx.outs} clean outs → "
            f"{ctx.rule_2_4[0]*100:.0f}% next card, {ctx.rule_2_4[1]*100:.0f}% by river "
            f"(rule of 2 & 4)."
        )

    if ctx.ev_by_action:
        ev_lines = sorted(ctx.ev_by_action.items(), key=lambda kv: -kv[1])
        out["ev"] = " · ".join(
            f"{ctx.ev_labels.get(name, name)}: {ev:+.1f}" for name, ev in ev_lines
        )
        out["verdict"] = f"Highest-EV play: **{ctx.verdict_label or ctx.verdict}**."

    # ---- Tier 2: range awareness ----
    if tier >= 2:
        if ctx.villain_archetype:
            out["villain_range"] = (
                f"Equity is computed vs. {ctx.villain_archetype}'s estimated range here "
                f"(not vs. a random hand)."
            )
        if ctx.blocker_note:
            out["blockers"] = ctx.blocker_note
        if ctx.position_note:
            out["position"] = ctx.position_note

    # ---- Tier 3: GTO framing ----
    if tier >= 3:
        if ctx.to_call > 0:
            out["mdf"] = (
                f"MDF = {ctx.mdf*100:.0f}% — to make villain indifferent to bluffing this size, "
                f"defend at least {ctx.mdf*100:.0f}% of your range. Pure bluffs need "
                f"α = {ctx.alpha*100:.0f}% folds to break even."
            )
        if ctx.equity_realization_note:
            out["equity_realization"] = ctx.equity_realization_note
        if ctx.range_type_note:
            out["range_type"] = ctx.range_type_note

    # ---- Tier 4: exploit / mixed strategies ----
    if tier >= 4:
        if ctx.exploit_note:
            out["exploit"] = ctx.exploit_note
        if ctx.mixed_strategy_note:
            out["mixed_strategy"] = ctx.mixed_strategy_note

    return out
