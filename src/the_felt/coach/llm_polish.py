"""LLM polish for coach explanations + Q&A.

Wraps Anthropic API calls. Falls back to deterministic templated prose when
no API key is configured (so the trainer keeps working offline).

Prompt caching: the system prompt + tier definition are static (cacheable);
only the per-decision JSON context varies.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import asdict

from the_felt.coach.analyzer import DecisionContext
from the_felt.coach.explain import render_tier

log = logging.getLogger(__name__)

_COACH_SYSTEM = """You are a poker probability coach embedded in a Texas Hold'em training app.
You explain decisions turn-by-turn in plain, encouraging language calibrated to the user's skill tier.

Tier guide:
- Tier 1 (Beginner): pot odds, equity vs required, outs (rule of 2 & 4), the verdict. Avoid jargon.
- Tier 2 (Intermediate): introduce hand-vs-range, position, blockers. Use range language.
- Tier 3 (Advanced): MDF/alpha, polarized vs linear vs merged, equity realization, board texture.
- Tier 4 (Expert): mixed strategies, exploit deviations vs the seated archetype, ICM if relevant.

Style:
- Direct, concrete. 2-4 short sentences. No filler.
- Always anchor on the actual numbers from the math block.
- If the user's recommended action differs from the EV-max action, say so and name the alternative.
- Never invent numbers — only use the values supplied in the context.
"""

_QA_SYSTEM = """You are a poker tutor inside a training app. Answer the user's specific question
about a hand they just played or are playing. Use the math context supplied. Stay grounded in the
numbers — never fabricate equities. Be concise (2-5 sentences). If the user's question can't be
answered from the context, say what additional info you'd need."""


def _get_client():
    """Lazily import + construct the Anthropic client if a key is set."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        from anthropic import AsyncAnthropic
        return AsyncAnthropic()
    except Exception:
        log.exception("anthropic SDK unavailable")
        return None


def _coach_model() -> str:
    return os.environ.get("COACH_MODEL", "claude-opus-4-7")


def _context_to_prompt(ctx: DecisionContext, tier: int, tier_strings: dict[str, str]) -> str:
    parts = [
        "## Game state",
        f"- Street: {ctx.street}",
        f"- Hero position: {ctx.hero_position}",
        f"- Hero hand: {' '.join(ctx.hero_cards)}",
        f"- Board: {' '.join(ctx.board) if ctx.board else '(preflop)'}",
        f"- Pot: {ctx.pot}",
        f"- To call: {ctx.to_call}",
        f"- Last aggressor: {ctx.last_aggressor or 'n/a'}",
        f"- Estimated villain archetype: {ctx.villain_archetype or 'unknown'} ({ctx.villain_range_size} combos)",
        "",
        "## Math",
        f"- Equity (vs estimated range): {ctx.equity*100:.1f}%",
        f"- Pot odds required: {ctx.pot_odds_required*100:.1f}%",
        f"- Edge: {ctx.edge*100:+.1f}%",
        f"- MDF: {ctx.mdf*100:.0f}%   alpha: {ctx.alpha*100:.0f}%",
        f"- Outs: {ctx.outs} ({ctx.rule_2_4[0]*100:.0f}% next, {ctx.rule_2_4[1]*100:.0f}% by river)",
        f"- EV by action: {', '.join(f'{k}={v:+.1f}' for k, v in sorted(ctx.ev_by_action.items(), key=lambda kv: -kv[1]))}",
        f"- Highest-EV action: {ctx.verdict}",
        "",
        f"## Tier {tier} highlights (already shown to user as bullet points):",
    ]
    for k, v in tier_strings.items():
        parts.append(f"- [{k}] {v}")
    parts.append("")
    parts.append("Write a 2-4 sentence coach explanation tailored to the user's tier. "
                 "Reference the verdict and one key number. Don't restate what's already in the bullet list.")
    return "\n".join(parts)


def _fallback_polish(ctx: DecisionContext, tier: int) -> str:
    """Deterministic prose when no LLM is available. Tier 1 is plain English.

    The polish is VERDICT-driven: we look at what the EV-max action is and
    explain WHY it's the right play. The spot category provides supporting
    context but doesn't override the verdict.
    """
    rec = ctx.verdict_label or ctx.verdict
    verdict_key = ctx.verdict
    win_pct = ctx.equity * 100
    need_pct = ctx.pot_odds_required * 100

    if tier == 1:
        return _t1_polish(ctx, verdict_key, rec, win_pct, need_pct)
    return _t2plus_polish(ctx, verdict_key, rec, win_pct, need_pct, tier)


_T1_ARCHETYPE_LABELS = {
    "TAG": "this opponent",
    "LAG": "this opponent",
    "Nit": "this opponent",
    "Calling Station": "this opponent",
    "Maniac": "this opponent",
    "Whale": "this opponent",
    "GTO Reg": "this opponent",
}


def _t1_polish(ctx: DecisionContext, verdict_key: str, rec: str, win_pct: float, need_pct: float) -> str:
    """Plain-English prose, verdict-driven. Never names archetype acronyms."""
    villain = _T1_ARCHETYPE_LABELS.get(ctx.villain_archetype or "", "your opponent")
    # No bet to face
    if ctx.to_call <= 0:
        if verdict_key in ("bet_value",):
            if win_pct >= 60:
                return (
                    f"You're ahead — about {win_pct:.0f}% to win. Bet to make weaker "
                    f"hands pay you off. Best play: {rec}."
                )
            return (
                f"Even though you're not a heavy favorite ({win_pct:.0f}%), {villain} "
                f"will fold often enough that betting still makes money. Best play: {rec}."
            )
        # Check is the recommendation
        return (
            f"You're at about {win_pct:.0f}% to win, and {villain} isn't going to "
            f"fold a check. Take the free card. Best play: {rec}."
        )

    # Facing a bet — verdict-driven
    if verdict_key == "fold":
        return (
            f"You'll only win about {win_pct:.0f}% of the time, and you need {need_pct:.0f}% "
            f"to break even. Best play: {rec}."
        )
    if verdict_key == "call":
        return (
            f"You'll win about {win_pct:.0f}% of the time, and you only need {need_pct:.0f}% "
            f"to make money. Calling is profitable. Best play: {rec}."
        )
    if verdict_key in ("raise_min", "raise_big"):
        # Raise can be for value (you're ahead) or as a (semi-)bluff
        if win_pct >= 60:
            return (
                f"You're likely ahead ({win_pct:.0f}% to win). Raising charges weaker "
                f"hands and builds the pot. Best play: {rec}."
            )
        if ctx.outs > 0:
            return (
                f"You only win about {win_pct:.0f}% at showdown, but you have {ctx.outs} cards "
                f"that can improve you AND {villain} will fold often enough to make this "
                f"profitable two ways. Best play: {rec}."
            )
        return (
            f"At showdown you'd only win {win_pct:.0f}%, but {villain} will fold often "
            f"enough that raising still makes money. Best play: {rec}."
        )

    # Fallback
    return f"With about {win_pct:.0f}% to win, the best play here is {rec}."


def _t2plus_polish(ctx: DecisionContext, verdict_key: str, rec: str, win_pct: float, need_pct: float, tier: int) -> str:
    """Tier 2+: poker terminology, verdict-driven."""
    villain = ctx.villain_archetype or "your opponent"
    if ctx.to_call <= 0:
        if verdict_key == "bet_value":
            base = (
                f"With {win_pct:.0f}% equity vs {villain}'s range, betting extracts value "
                f"from worse hands and protects against draws. {rec}."
            )
        else:
            base = (
                f"No bet to face. With {win_pct:.0f}% equity, checking is best — taking "
                f"a free card costs nothing."
            )
    elif verdict_key == "fold":
        base = (
            f"Your {win_pct:.0f}% equity vs. {villain} falls short of the {need_pct:.0f}% "
            f"required — folding is +EV."
        )
    elif verdict_key == "call":
        base = (
            f"Your {win_pct:.0f}% equity beats the {need_pct:.0f}% you need — calling is +EV."
        )
    elif verdict_key in ("raise_min", "raise_big"):
        if win_pct >= 60:
            base = (
                f"Value raise: {win_pct:.0f}% equity. Raising builds the pot vs worse and "
                f"charges draws."
            )
        elif ctx.outs > 0:
            base = (
                f"Semi-bluff raise: {win_pct:.0f}% equity + {ctx.outs} outs + fold equity. "
                f"Two ways to win."
            )
        else:
            base = (
                f"Bluff raise: {win_pct:.0f}% equity at showdown but {villain} folds often "
                f"enough that this is +EV."
            )
    else:
        base = f"Best play: {rec}."

    if tier >= 2 and ctx.spot_t2_note:
        base += " " + ctx.spot_t2_note
    if tier >= 3 and ctx.equity_realization_note:
        base += " " + ctx.equity_realization_note
    if tier >= 3 and ctx.spot_t3_note:
        base += " " + ctx.spot_t3_note
    if tier >= 4 and ctx.exploit_note:
        base += " " + ctx.exploit_note
    if tier >= 4 and ctx.spot_t4_note:
        base += " " + ctx.spot_t4_note
    return base

    return _t2plus_polish(ctx, ctx.verdict, rec, win_pct, need_pct, tier)


async def polish(ctx: DecisionContext, tier: int, timeout_s: float = 6.0) -> str:
    """Return a polished prose explanation for the current decision."""
    tier_strings = render_tier(ctx, tier)
    client = _get_client()
    if client is None:
        return _fallback_polish(ctx, tier)

    prompt = _context_to_prompt(ctx, tier, tier_strings)
    try:
        resp = await asyncio.wait_for(
            client.messages.create(
                model=_coach_model(),
                max_tokens=400,
                system=[
                    {"type": "text", "text": _COACH_SYSTEM, "cache_control": {"type": "ephemeral"}},
                ],
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=timeout_s,
        )
        # Extract text
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                return block.text.strip()
        return _fallback_polish(ctx, tier)
    except Exception:
        log.exception("LLM polish failed; falling back")
        return _fallback_polish(ctx, tier)


async def answer_question(question: str, ctx: DecisionContext, tier: int, timeout_s: float = 8.0) -> str:
    """Answer a user follow-up question about the current decision."""
    tier_strings = render_tier(ctx, tier)
    client = _get_client()
    if client is None:
        # Templated answer: just echo math relevant to the question keywords
        return _fallback_qa(question, ctx)

    prompt = _context_to_prompt(ctx, tier, tier_strings) + f"\n\nUser question: {question}\n\nAnswer:"
    try:
        resp = await asyncio.wait_for(
            client.messages.create(
                model=_coach_model(),
                max_tokens=500,
                system=[
                    {"type": "text", "text": _QA_SYSTEM, "cache_control": {"type": "ephemeral"}},
                ],
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=timeout_s,
        )
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                return block.text.strip()
        return _fallback_qa(question, ctx)
    except Exception:
        log.exception("LLM QA failed; falling back")
        return _fallback_qa(question, ctx)


def _fallback_qa(question: str, ctx: DecisionContext) -> str:
    q = question.lower()
    if "equity" in q or "%" in q:
        return (
            f"Your equity here is {ctx.equity*100:.1f}% (vs. {ctx.villain_archetype or 'opponent'}'s "
            f"estimated range of {ctx.villain_range_size} combos). "
            f"You need {ctx.pot_odds_required*100:.1f}% to break even on a call."
        )
    if "pot odds" in q or "price" in q:
        return f"Pot odds = {ctx.to_call} to call into {ctx.pot - ctx.to_call} → {ctx.pot_odds_required*100:.1f}% needed."
    if "mdf" in q or "defend" in q:
        return f"MDF here is {ctx.mdf*100:.0f}% — that's the share of your range you need to defend to make villain indifferent to bluffing."
    if "fold" in q or "should i fold" in q:
        return f"EV(fold)=0 vs. EV({ctx.verdict})={ctx.ev_by_action.get(ctx.verdict, 0):+.1f}. Folding here loses {ctx.ev_by_action.get(ctx.verdict, 0):.1f} chips of EV." if ctx.verdict != "fold" else f"Folding is the highest-EV play."
    if "raise" in q:
        if "raise_min" in ctx.ev_by_action:
            return f"EV(raise_min) = {ctx.ev_by_action['raise_min']:+.1f}. " + (ctx.exploit_note or "")
        return "Raising isn't currently legal in this spot."
    return (
        f"Current verdict: {ctx.verdict} (EV {ctx.ev_by_action.get(ctx.verdict, 0):+.1f}). "
        f"Set ANTHROPIC_API_KEY for full natural-language coaching."
    )
