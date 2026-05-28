"""Bluff-spot classifier + opponent-aware bluffing heuristics.

We map every decision to one of a small set of *spot types* so the coach can
explain WHY a play is correct — not just point to the EV-max button. The
classifier is heuristic, but the heuristics are grounded in the same math the
analyzer uses (equity, pot odds, fold equity, MDF).

Spot taxonomy
-------------
- `value_raise`   — strong hand, want to build the pot and get value from worse
- `value_bet`     — strong-ish hand, no bet to face, want to bet for value
- `value_call`    — comfortably ahead of villain's range, just take the cheap card
- `bluff_catch`   — marginal showdown value, calling only because villain bluffs enough
- `semi_bluff`    — air or weak made, BUT has outs that give backup equity
- `pure_bluff`    — air, no outs, but fold equity makes it +EV
- `marginal`      — borderline spot, mix of fold/call
- `give_up`       — bad equity, no fold equity, just check/fold

Each spot returns a short label + a list of pedagogical notes that
`coach.explain.render_tier` can surface at the appropriate tier.
"""

from __future__ import annotations

from dataclasses import dataclass

from the_felt.agents.archetype import Archetype


@dataclass(frozen=True, slots=True)
class SpotClassification:
    spot: str                          # one of the taxonomy strings above
    intent: str                        # "for_value" | "as_bluff" | "as_bluff_catch" | "to_realize" | "give_up"
    has_fold_equity: bool              # do we expect villain to fold often enough?
    has_backup_equity: bool             # do we have outs as safety net?
    # Pedagogical notes by tier
    t1_note: str | None = None
    t2_note: str | None = None
    t3_note: str | None = None
    t4_note: str | None = None


# Threshold values are tuned to be heuristic but consistent with the
# archetype parameters and the analyzer's value/raise cutoffs.
_VALUE_RAISE_EQ = 0.75
_VALUE_BET_EQ = 0.62
_AHEAD_EQ = 0.55
_BLUFF_CATCH_FLOOR = 0.18


def classify_spot(
    *,
    equity: float,
    pot_odds_required: float,
    has_bet_to_face: bool,
    outs: int,
    by_river_pct: float,
    archetype: Archetype | None,
    street: str,
    position_aware: bool = True,
) -> SpotClassification:
    """Classify the current decision spot for pedagogy.

    `archetype` is the primary villain we're computing equity against. It
    drives whether bluffs and bluff-catches are profitable.
    """
    has_draws = outs >= 6 or by_river_pct >= 0.30
    # How willing is the opponent to fold to our aggression?
    fold_propensity = archetype.fold_to_3bet if archetype else 0.55
    # How often does the opponent bluff at us?
    villain_bluff_rate = archetype.bluff_freq if archetype else 0.10

    if has_bet_to_face:
        return _classify_facing_bet(
            equity, pot_odds_required, has_draws, outs, archetype,
            fold_propensity, villain_bluff_rate,
        )
    return _classify_no_bet(
        equity, has_draws, outs, archetype, fold_propensity, street,
    )


def _classify_facing_bet(
    equity: float,
    pot_odds_required: float,
    has_draws: bool,
    outs: int,
    arch: Archetype | None,
    fold_propensity: float,
    villain_bluff_rate: float,
) -> SpotClassification:
    edge = equity - pot_odds_required

    # Clear value raise zone
    if equity >= _VALUE_RAISE_EQ:
        return SpotClassification(
            spot="value_raise",
            intent="for_value",
            has_fold_equity=fold_propensity > 0.4,
            has_backup_equity=False,
            t1_note=(
                f"You're likely ahead — about {equity*100:.0f}% to win. "
                f"Raise to charge weaker hands and build the pot."
            ),
            t2_note=(
                f"Strong equity ({equity*100:.0f}%). Raise for value — even when called you're profitable."
            ),
            t3_note=(
                "Polarized line: raise puts your strongest hands into the pot. "
                "Bluffs at the same sizing keep your range balanced."
            ),
        )

    # Comfortably ahead — just call
    if edge > 0.10:
        return SpotClassification(
            spot="value_call",
            intent="for_value",
            has_fold_equity=False,
            has_backup_equity=False,
            t1_note=(
                f"You're ahead — {equity*100:.0f}% to win, only {pot_odds_required*100:.0f}% needed. "
                f"Calling is comfortably profitable."
            ),
            t2_note=(
                f"+{edge*100:.0f}% edge. You realize equity by calling. Don't raise — you "
                f"only fold out worse hands and price in draws."
            ),
        )

    # Semi-bluff raise zone — weak but with strong draws + fold equity
    if has_draws and equity < pot_odds_required + 0.10 and fold_propensity > 0.45:
        return SpotClassification(
            spot="semi_bluff",
            intent="as_bluff",
            has_fold_equity=True,
            has_backup_equity=True,
            t1_note=(
                f"You have {outs} cards that improve your hand. Even if your opponent calls, "
                f"you can still win. Raising is profitable two ways: they fold, or they call and you draw out."
            ),
            t2_note=(
                f"Semi-bluff: equity {equity*100:.0f}% + fold equity. The {outs} outs are your "
                f"backup equity — better than pure-air bluffs."
            ),
            t3_note=(
                "Semi-bluffs are the best bluffs in poker — you have two ways to win. "
                "Solver lines pick draws over air for raise sizings."
            ),
            t4_note=(
                f"vs {arch.name if arch else 'opponent'}: they fold ~{int(fold_propensity*100)}% to aggression, "
                f"so even pure air would be marginal — the draw makes this profitable."
            ),
        )

    # Bluff-catch zone — marginal showdown value, profitability depends on villain bluffing enough
    if equity > _BLUFF_CATCH_FLOOR and edge > -0.15:
        # How much do we need villain to bluff for this to be a profitable call?
        bluffs_needed = max(0.0, pot_odds_required - equity * 0.5)  # rough heuristic
        call_profitable = villain_bluff_rate > bluffs_needed
        return SpotClassification(
            spot="bluff_catch",
            intent="as_bluff_catch",
            has_fold_equity=False,
            has_backup_equity=False,
            t1_note=(
                f"You hold a hand that beats their bluffs but loses to their value bets. "
                f"{'Call — they bluff often enough to make this profitable.' if call_profitable else 'Fold — they rarely bluff here, so calling loses on average.'}"
            ),
            t2_note=(
                f"Bluff-catcher: equity {equity*100:.0f}%, need {pot_odds_required*100:.0f}%. "
                f"Profitability depends entirely on villain's bluff frequency. "
                f"{arch.name if arch else 'Opponent'} bluffs ~{int(villain_bluff_rate*100)}% — "
                f"{'call' if call_profitable else 'fold'}."
            ),
            t3_note=(
                f"You're at the very bottom of your range here. MDF tells you how many "
                f"bluff-catchers you must defend; this hand's value vs. villain's bluffs determines if it's a defender."
            ),
            t4_note=(
                f"Exploit: vs {arch.name if arch else 'opponent'} ({int(villain_bluff_rate*100)}% bluffs), "
                f"calling needs ~{int(bluffs_needed*100)}% bluffs. "
                f"{'Profitable call.' if call_profitable else 'EV+ to fold.'}"
            ),
        )

    # Pure give-up — bad equity, no fold equity, no draws
    if equity < _BLUFF_CATCH_FLOOR and not has_draws:
        return SpotClassification(
            spot="give_up",
            intent="give_up",
            has_fold_equity=False,
            has_backup_equity=False,
            t1_note=(
                f"You'll only win about {equity*100:.0f}% of the time, and you need {pot_odds_required*100:.0f}%. "
                f"Folding is correct."
            ),
            t2_note=(
                f"Equity {equity*100:.0f}% << required {pot_odds_required*100:.0f}%. Fold."
            ),
        )

    return SpotClassification(
        spot="marginal",
        intent="give_up",
        has_fold_equity=False,
        has_backup_equity=has_draws,
        t1_note=(
            f"This is close — about a {equity*100:.0f}% hand against {pot_odds_required*100:.0f}% needed. "
            f"Take the closer-to-EV-neutral play and move on."
        ),
        t2_note=(
            f"Borderline ({edge*100:+.1f}% edge). Mixing fold and call here is fine."
        ),
    )


def _classify_no_bet(
    equity: float,
    has_draws: bool,
    outs: int,
    arch: Archetype | None,
    fold_propensity: float,
    street: str,
) -> SpotClassification:
    # Value bet zone
    if equity >= _VALUE_BET_EQ:
        return SpotClassification(
            spot="value_bet",
            intent="for_value",
            has_fold_equity=False,
            has_backup_equity=False,
            t1_note=(
                f"You're ahead — about {equity*100:.0f}% to win. Bet to get worse hands to pay you off."
            ),
            t2_note=(
                f"Value bet: {equity*100:.0f}% equity. Sized 50–66% pot gets worse hands "
                f"to call while protecting against draws."
            ),
            t3_note=(
                "Polarized range: value bets with strong made hands; balance with bluffs in proportion to bet size."
            ),
        )

    # Semi-bluff bet — has draws, hasn't made yet
    if has_draws and equity < _VALUE_BET_EQ:
        return SpotClassification(
            spot="semi_bluff",
            intent="as_bluff",
            has_fold_equity=fold_propensity > 0.40,
            has_backup_equity=True,
            t1_note=(
                f"You have {outs} cards that can improve you. Betting now wins the pot if they fold — "
                f"and if they call, you still have a real chance to win on the next card."
            ),
            t2_note=(
                f"Semi-bluff bet: fold equity + {outs} outs. Two ways to win = best bluff EV in poker."
            ),
            t3_note=(
                "Semi-bluffs realize equity AND apply pressure. Prefer draw hands over pure air "
                "for any bluffing frequency."
            ),
            t4_note=(
                f"vs {arch.name if arch else 'opponent'}: they fold ~{int(fold_propensity*100)}% — "
                f"raise sizing of ~⅔ pot maximizes fold equity without committing on a miss."
            ),
        )

    # Pure bluff bet — air, but fold equity is high enough to be +EV
    if equity < 0.30 and fold_propensity > 0.55:
        return SpotClassification(
            spot="pure_bluff",
            intent="as_bluff",
            has_fold_equity=True,
            has_backup_equity=False,
            t1_note=(
                f"Your hand can't win at showdown, but your opponent will fold often enough "
                f"that betting still makes money. This is a pure bluff."
            ),
            t2_note=(
                f"Pure bluff: {equity*100:.0f}% equity, ~{int(fold_propensity*100)}% fold rate. "
                f"You're paying for fold equity directly."
            ),
            t3_note=(
                "Pick bluffs with no showdown value and good blockers to villain's value combos. "
                "Don't bluff with hands that could win at showdown."
            ),
            t4_note=(
                f"vs {arch.name if arch else 'opponent'} (bluffs back ~{int(arch.bluff_freq*100) if arch else 10}%, "
                f"folds ~{int(fold_propensity*100)}%) — bluff frequency should be high here."
            ),
        )

    # Check it back — no value, no fold equity, no draws
    return SpotClassification(
        spot="give_up",
        intent="give_up",
        has_fold_equity=False,
        has_backup_equity=has_draws,
        t1_note=(
            f"You're behind ({equity*100:.0f}%) and your opponent isn't going anywhere. "
            f"Check, see the next card for free."
        ),
        t2_note=(
            f"No value, no fold equity. Check to realize what equity you have."
        ),
    )


def exploit_summary(arch: Archetype | None) -> str | None:
    """One-line opponent-specific bluffing rule. Used at Tier 4+."""
    if arch is None:
        return None
    if arch.name == "Calling Station":
        return "Calling Station — never bluff, always value-bet thin."
    if arch.name == "Whale":
        return "Whale — extract maximum value, don't bluff."
    if arch.name == "Nit":
        return "Nit — bluff freely, fold to their aggression."
    if arch.name == "Maniac":
        return "Maniac — call lighter (they bluff too much), don't bluff back."
    if arch.name == "LAG":
        return "LAG — they bluff often; widen your bluff-catching, tighten your own bluffs."
    if arch.name == "TAG":
        return "TAG — balanced; bluff selectively in position, look for thin value."
    if arch.name == "GTO Reg":
        return "GTO Reg — they're balanced; small exploits only, stay close to GTO yourself."
    return None
