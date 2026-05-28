"""Live coach narration for opponent actions.

After each non-hero action (or board change), generate a short pedagogical
note that teaches the user to update their read in real time. The goal is to
make the user *think along with the coach* rather than only react when it's
their turn.

The narration is generated from:
- Who acted (archetype + position)
- What they did (raise/call/check/bet/fold with size)
- The street and board texture
- The hand context (was this a 3-bet? a c-bet?)
"""

from __future__ import annotations

from dataclasses import dataclass

from the_felt.agents.archetype import REGISTRY, Archetype
from the_felt.agents.texture import Texture
from the_felt.engine.table import Player


@dataclass(frozen=True, slots=True)
class Narration:
    """A short pedagogical note tied to a single observable event."""

    kind: str         # "preflop_open" | "preflop_3bet" | "preflop_call" | "flop_cbet" | ...
    headline: str     # e.g. "Sam raised to 30"
    insight: str      # the teaching content
    range_hint: str | None = None   # short text like "top 5% — QQ+, AK"


# Position descriptors used in narration
POSITION_CATEGORIES = {
    "UTG": ("early", "tight"),
    "UTG+1": ("early", "tight"),
    "MP": ("middle", "medium"),
    "LJ": ("middle", "medium"),
    "HJ": ("late-ish", "wider"),
    "CO": ("the cutoff", "wide"),
    "BTN": ("the button", "very wide"),
    "SB": ("the small blind", "medium"),
    "BB": ("the big blind", "wide"),
}


def narrate_preflop_action(
    actor: Player,
    arch: Archetype,
    action: str,
    amount: int,
    bb: int,
    raises_before: int,
    facing_raise: bool,
) -> Narration | None:
    """Generate a narration for a preflop player action."""
    name = actor.name.split(" (")[0]
    pos_desc, pos_loosen = POSITION_CATEGORIES.get(actor.position.value if actor.position else "", ("an unknown position", "?"))
    style = _style_word(arch)

    if action in ("raise", "bet") and not facing_raise:
        # First raise in the hand
        size_bb = amount / max(bb, 1)
        # Estimate their range
        vpip = arch.vpip
        range_pct = min(int(vpip * 100 * (1.3 if "BTN" in (actor.position.value if actor.position else "") else 1.0)), 60)
        range_desc = _range_description(arch, range_pct, opening=True)
        insight = (
            f"{name} is a {style} player opening from {pos_desc}. Their {size_bb:.1f}-BB open "
            f"means they're playing the top ~{range_pct}% of hands — {range_desc}."
        )
        return Narration(
            kind="preflop_open",
            headline=f"{name} opens to {amount}",
            insight=insight,
            range_hint=range_desc,
        )

    if action in ("raise", "bet") and facing_raise and raises_before == 1:
        # 3-bet
        three_bet_pct = max(2, int(arch.three_bet * 100))
        range_desc = _three_bet_range_description(arch)
        insight = (
            f"A 3-bet from a {style} player is strong — only their top ~{three_bet_pct}% of hands. "
            f"Likely {range_desc}. Tighten up your continuing range."
        )
        return Narration(
            kind="preflop_3bet",
            headline=f"{name} 3-bets to {amount}",
            insight=insight,
            range_hint=range_desc,
        )

    if action in ("raise", "bet") and raises_before >= 2:
        # 4-bet+
        return Narration(
            kind="preflop_4bet",
            headline=f"{name} 4-bets to {amount}",
            insight=(
                f"A 4-bet means {name} has the absolute top of their range — usually KK+ or AK. "
                f"Folding everything but AA, KK is the standard line here."
            ),
            range_hint="KK+, AK",
        )

    if action == "call" and facing_raise:
        # Flat call vs a raise
        range_desc = _call_range_description(arch)
        insight = (
            f"{name} flat-calls — they have a hand strong enough to play but not strong enough "
            f"to 3-bet. Typically {range_desc}."
        )
        return Narration(
            kind="preflop_call",
            headline=f"{name} calls {amount}",
            insight=insight,
            range_hint=range_desc,
        )

    if action == "fold":
        # Folds are usually not interesting, but a fold from a wide player IS
        if arch.vpip > 0.30 and raises_before >= 1:
            return Narration(
                kind="preflop_fold",
                headline=f"{name} folds",
                insight=f"Even a loose player like {name} folded — that means the raise was big enough to scare them off.",
            )
        return None

    return None


def narrate_postflop_action(
    actor: Player,
    arch: Archetype,
    action: str,
    amount: int,
    pot: int,
    street: str,
    texture: Texture | None,
    is_preflop_aggressor: bool,
    facing_bet: bool,
) -> Narration | None:
    name = actor.name.split(" (")[0]
    style = _style_word(arch)
    pot_frac = (amount / max(pot, 1)) if amount else 0

    if action in ("bet", "raise") and not facing_bet:
        # Bet without a bet to face → c-bet (if PFA) or donk-bet
        if is_preflop_aggressor:
            # C-bet
            cbet_pct = int(arch.cbet_freq * 100)
            if pot_frac >= 0.7:
                size_word = "big (overbet-ish)"
                meaning = "polarized — strong made hands and bluffs, less in the middle"
            elif pot_frac >= 0.45:
                size_word = "medium (~⅔ pot)"
                meaning = "standard value-betting size"
            else:
                size_word = "small (~⅓ pot)"
                meaning = "wide range — protects checks, low fold equity"
            insight = (
                f"{name} c-bets ({style} players c-bet ~{cbet_pct}% of flops). "
                f"This size is {size_word} — {meaning}."
            )
            return Narration(
                kind="cbet",
                headline=f"{name} bets {amount} ({pot_frac*100:.0f}% pot)",
                insight=insight,
            )
        # Donk-bet (rare)
        return Narration(
            kind="donk_bet",
            headline=f"{name} donks {amount}",
            insight=(
                f"A donk-bet (betting INTO the preflop raiser) usually signals a specific made "
                f"hand on this board — often two pair or a strong draw."
            ),
        )

    if action in ("bet", "raise") and facing_bet:
        # Check-raise or re-raise
        return Narration(
            kind="raise",
            headline=f"{name} raises to {amount}",
            insight=(
                f"A raise from {name} ({style}) on the {street} usually means strong made hands "
                f"or a semi-bluff with a big draw. Your range needs to be condensed to call."
            ),
        )

    if action == "call" and facing_bet:
        meaning = _call_meaning(arch, street)
        return Narration(
            kind="postflop_call",
            headline=f"{name} calls {amount}",
            insight=f"A call from {name} on the {street}: {meaning}",
        )

    if action == "check":
        if is_preflop_aggressor:
            return Narration(
                kind="cbet_check",
                headline=f"{name} checks",
                insight=(
                    f"{name} was the preflop aggressor and chose to check — likely a give-up "
                    f"or a slow-play with a big hand. If you bet, you can often pick up the pot."
                ),
            )
        return None

    return None


def narrate_board(street: str, board: list[str], texture: Texture) -> Narration:
    """Narrate the new board cards and what they mean for ranges."""
    board_str = " ".join(board)
    if street == "flop":
        if texture.monotone:
            insight = f"Three of the same suit — a monotone flop. Anyone with a card of that suit has at least a flush draw. Ranges narrow sharply."
        elif texture.paired:
            insight = f"Paired board — fewer two-pair combos in play, more trips for anyone with the pair card."
        elif texture.connected and texture.high_card_present:
            insight = f"Wet, high-card flop — favors the preflop aggressor's range (more big pairs and broadway). Many draws live too."
        elif texture.high_card_present and not texture.connected:
            insight = f"Dry, high-card flop — strongly favors the preflop aggressor. Few draws to defend against."
        elif not texture.high_card_present and texture.connected:
            insight = f"Low, connected flop — favors the caller's range. Big pairs (AA, KK) lose some equity to two-pair hands."
        else:
            insight = f"Standard flop texture. Neither side has a dominant range advantage."
    elif street == "turn":
        if texture.monotone:
            insight = f"A third (or fourth) suit card — flushes are live. If your opponent's range has the suit, expect aggression."
        elif texture.paired:
            insight = f"Board pairs up — full house possibilities open. Big bets get more credit."
        else:
            insight = f"Turn card. Anyone who called the flop with a draw now has one card left to hit."
    else:  # river
        insight = f"Final card. No more draws — equity is locked in. From here, it's about value vs bluffs in each range."
    return Narration(
        kind=f"board_{street}",
        headline=f"{street.upper()}: {board_str}",
        insight=insight,
    )


# =============================================================
# Helpers
# =============================================================

def _style_word(arch: Archetype) -> str:
    name = arch.name.lower()
    if "nit" in name: return "very tight"
    if "tag" in name: return "tight-aggressive"
    if "lag" in name: return "loose-aggressive"
    if "calling" in name: return "calling-station-style"
    if "maniac" in name: return "hyper-aggressive"
    if "whale" in name: return "loose-passive"
    if "gto" in name: return "balanced (GTO-style)"
    return "average"


def _range_description(arch: Archetype, pct: int, opening: bool) -> str:
    if pct <= 8:
        return "pretty much just QQ+, AK"
    if pct <= 15:
        return "premium pairs, AK, AQ, suited broadways"
    if pct <= 25:
        return "all pairs, broadway hands, suited connectors"
    if pct <= 40:
        return "any pair, most broadways, suited connectors, suited aces"
    if pct <= 55:
        return "nearly any face-card combo, all pairs, lots of suited junk"
    return "more than half of all hands — very wide range"


def _three_bet_range_description(arch: Archetype) -> str:
    if arch.three_bet < 0.04:
        return "JJ+ and AK only"
    if arch.three_bet < 0.07:
        return "TT+, AQ+, sometimes a polarized bluff"
    if arch.three_bet < 0.10:
        return "99+, AJ+, KQ, plus a few suited bluffs"
    return "wider — they balance value with light 3-bets, expect more bluffs"


def _call_range_description(arch: Archetype) -> str:
    if arch.vpip < 0.18:
        return "pocket pairs to set-mine, AQ-AJ"
    if arch.vpip < 0.25:
        return "pocket pairs, suited broadways, suited connectors"
    if arch.vpip < 0.40:
        return "any pair, most suited cards, weak broadways"
    return "almost anything that could make a pair — they call too much"


def _call_meaning(arch: Archetype, street: str) -> str:
    if arch.vpip > 0.35 and arch.afq < 0.25:
        return f"a station like this calls with **any pair or any draw** — don't bluff them, value-bet thin."
    if arch.vpip < 0.18:
        return f"a tight player calling means they have at least a medium-strength made hand or a strong draw."
    return f"a typical call — likely a marginal made hand or a draw."
