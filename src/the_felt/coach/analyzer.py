"""Compute the math behind a decision: equity, pot odds, MDF, EV for each legal action.

Phase 2: equity is computed vs. an archetype-estimated villain range rather
than vs. random, and we surface tier-2/3/4 hints (blockers, position, MDF,
equity realization, exploit) that `coach.explain.render_tier` filters by tier.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from the_felt.agents.archetype import REGISTRY, Archetype
from the_felt.agents.villain_range import estimate_range
from the_felt.agents.texture import analyze as analyze_texture
from the_felt.cards import to_str as card_to_str
from the_felt.coach.bluffing import classify_spot, exploit_summary
from the_felt.engine.hand import ActionRequest, Hand
from the_felt.engine.table import Player
from the_felt.equity.monte_carlo import equity_vs_random, equity_vs_range
from the_felt.probability.ev import ev_call, ev_fold, ev_raise
from the_felt.probability.mdf import alpha, mdf
from the_felt.probability.outs import count_outs, rule_of_2_and_4
from the_felt.probability.pot_odds import pot_odds
from the_felt.types import Position, Street


@dataclass
class DecisionContext:
    """All the math + qualitative notes relevant to a single decision point."""

    # ---- Tier 1: math ----
    hero_cards: list[str]
    board: list[str]
    street: str
    pot: int
    to_call: int
    equity: float                  # vs estimated villain range (or random fallback)
    pot_odds_required: float
    edge: float
    mdf: float
    alpha: float
    outs: int
    rule_2_4: tuple[float, float]
    ev_by_action: dict[str, float] = field(default_factory=dict)
    ev_labels: dict[str, str] = field(default_factory=dict)  # internal_key → display label
    verdict: str = ""             # internal_key of the best EV action
    verdict_label: str = ""        # display label for the verdict
    notes: list[str] = field(default_factory=list)
    # ---- Tier 2+: range/position context ----
    villain_archetype: str | None = None
    villain_range_size: int = 0    # number of combos in estimated range
    position_note: str | None = None
    blocker_note: str | None = None
    # ---- Tier 3: GTO framing ----
    equity_realization_note: str | None = None
    range_type_note: str | None = None
    # ---- Tier 4: exploit / mixed ----
    exploit_note: str | None = None
    mixed_strategy_note: str | None = None
    # ---- Bluffing lesson ----
    spot: str = "marginal"               # value_raise|value_bet|value_call|bluff_catch|semi_bluff|pure_bluff|marginal|give_up
    spot_intent: str = "for_value"        # for_value | as_bluff | as_bluff_catch | to_realize | give_up
    has_fold_equity: bool = False
    has_backup_equity: bool = False
    spot_t1_note: str | None = None
    spot_t2_note: str | None = None
    spot_t3_note: str | None = None
    spot_t4_note: str | None = None
    # ---- raw inputs for LLM polish ----
    hero_position: str | None = None
    last_aggressor: str | None = None


def compute_decision_context(
    hand: Hand,
    req: ActionRequest,
    hero: Player,
    *,
    equity_samples: int = 2000,
    rng: random.Random | None = None,
) -> DecisionContext:
    rng = rng or random.Random()
    pot = req.pot
    to_call = req.to_call
    board = hand.board

    # ---- Identify the "primary villain" to estimate a range against ----
    villain = _pick_primary_villain(hand, hero)
    arch = REGISTRY.get(villain.archetype_name) if villain and villain.archetype_name else None

    # ---- Equity ----
    if villain is not None and arch is not None:
        villain_combos = estimate_range(hand, villain, arch, blocked_cards=hero.hole_cards + board)
        if villain_combos:
            eq = equity_vs_range(
                hero.hole_cards, villain_combos, board=board, n=equity_samples, rng=rng,
            )
            range_size = len(villain_combos)
        else:
            eq = equity_vs_random(hero.hole_cards, board, n=equity_samples, rng=rng)
            range_size = 0
    else:
        eq = equity_vs_random(hero.hole_cards, board, n=equity_samples, rng=rng)
        range_size = 0

    # ---- Pot odds, MDF, alpha ----
    po_req = pot_odds(to_call, pot) if to_call > 0 else 0.0
    edge = eq - po_req
    if to_call > 0:
        pot_before_bet = max(0, pot - to_call)
        mdf_val = mdf(pot_before_bet, to_call)
        alpha_val = alpha(to_call, pot_before_bet)
    else:
        mdf_val = 1.0
        alpha_val = 0.0

    # ---- Outs ----
    outs = 0
    r24 = (0.0, 0.0)
    if req.street in (Street.FLOP, Street.TURN):
        outs = count_outs(hero.hole_cards, board)
        r24 = rule_of_2_and_4(outs, req.street.value)

    # ---- EV per action ----
    # Keep stable internal keys (used by the evaluator); also emit human labels.
    ev_actions: dict[str, float] = {"fold": ev_fold()}
    ev_labels: dict[str, str] = {"fold": "Fold"}
    if req.legal.can_check:
        ev_actions["check"] = 0.0
        ev_labels["check"] = "Check"
    if req.legal.can_call:
        ev_actions["call"] = ev_call(eq, to_call, pot)
        ev_labels["call"] = f"Call {to_call}"
    if req.legal.can_raise:
        fold_prob = max(0.0, 1.0 - mdf_val) if to_call > 0 else 0.40
        commit_min = req.legal.min_raise_to - hero.committed_street
        ev_actions["raise_min"] = ev_raise(
            equity_when_called=eq,
            raise_amount=commit_min,
            pot_before_raise=pot,
            to_call_before=to_call,
            fold_probability=fold_prob,
        )
        ev_labels["raise_min"] = f"Raise to {req.legal.min_raise_to}"
        commit_large = min(int(commit_min * 2.5), req.legal.max_raise_to - hero.committed_street)
        if commit_large > commit_min:
            target_large = hero.committed_street + commit_large
            ev_actions["raise_big"] = ev_raise(
                equity_when_called=eq,
                raise_amount=commit_large,
                pot_before_raise=pot,
                to_call_before=to_call,
                fold_probability=min(0.95, fold_prob + 0.10),
            )
            ev_labels["raise_big"] = f"Raise big to {target_large}"
    if req.legal.can_bet:
        commit = max(int(pot * 0.66), hand.table.bb)
        ev_actions["bet_value"] = ev_raise(
            equity_when_called=eq,
            raise_amount=commit,
            pot_before_raise=pot,
            to_call_before=0,
            fold_probability=0.40,
        )
        ev_labels["bet_value"] = f"Bet {commit} (about ⅔ pot)"

    verdict = max(ev_actions, key=lambda k: ev_actions[k]) if ev_actions else ""
    verdict_label = ev_labels.get(verdict, verdict)

    notes: list[str] = []
    if to_call > 0 and edge > 0.05:
        notes.append("Pot odds give you the right price — equity exceeds required.")
    elif to_call > 0 and edge < -0.05:
        notes.append("Pot odds are unfavorable — equity below required.")
    if outs > 0 and req.street == Street.FLOP:
        notes.append(f"{outs} outs ≈ {int(r24[0]*100)}% next card, {int(r24[1]*100)}% by river.")

    # ---- Tier 2: positional & range notes ----
    position_note = _position_note(hero.position, req.street)
    blocker_note = _blocker_note(hero.hole_cards, arch) if arch else None

    # ---- Tier 3: GTO framing ----
    texture = analyze_texture(board) if board else None
    eq_real_note = _equity_realization_note(texture, hero.position, req.street) if texture else None
    range_type_note = _range_type_note(texture, hand, hero, arch) if arch else None

    # ---- Tier 4: exploit / mixed ----
    exploit_note = _exploit_note(arch, eq, po_req) if arch else None
    mixed_note = _mixed_strategy_note(eq, edge, req.street)

    # ---- Bluffing lesson: classify the spot ----
    spot = classify_spot(
        equity=eq,
        pot_odds_required=po_req,
        has_bet_to_face=to_call > 0,
        outs=outs,
        by_river_pct=r24[1],
        archetype=arch,
        street=req.street.value,
    )
    # Reconcile spot with the EV-max verdict: if the verdict is a bet/raise
    # but we classified as value_call, upgrade to value_bet/value_raise so the
    # UI label matches the action being recommended.
    if verdict in ("raise_min", "raise_big") and spot.spot in ("value_call", "marginal"):
        from the_felt.coach.bluffing import SpotClassification
        spot = SpotClassification(
            spot="value_raise",
            intent="for_value",
            has_fold_equity=spot.has_fold_equity,
            has_backup_equity=spot.has_backup_equity,
            t1_note=spot.t1_note,
            t2_note=spot.t2_note,
            t3_note=spot.t3_note,
            t4_note=spot.t4_note,
        )
    elif verdict == "bet_value" and spot.spot in ("value_call", "marginal", "give_up"):
        from the_felt.coach.bluffing import SpotClassification
        spot = SpotClassification(
            spot="value_bet",
            intent="for_value",
            has_fold_equity=spot.has_fold_equity,
            has_backup_equity=spot.has_backup_equity,
            t1_note=spot.t1_note,
            t2_note=spot.t2_note,
            t3_note=spot.t3_note,
            t4_note=spot.t4_note,
        )

    return DecisionContext(
        hero_cards=[card_to_str(c) for c in hero.hole_cards],
        board=[card_to_str(c) for c in board],
        street=req.street.value,
        pot=pot,
        to_call=to_call,
        equity=eq,
        pot_odds_required=po_req,
        edge=edge,
        mdf=mdf_val,
        alpha=alpha_val,
        outs=outs,
        rule_2_4=r24,
        ev_by_action=ev_actions,
        ev_labels=ev_labels,
        verdict=verdict,
        verdict_label=verdict_label,
        notes=notes,
        villain_archetype=arch.name if arch else None,
        villain_range_size=range_size,
        position_note=position_note,
        blocker_note=blocker_note,
        equity_realization_note=eq_real_note,
        range_type_note=range_type_note,
        exploit_note=exploit_note,
        mixed_strategy_note=mixed_note,
        spot=spot.spot,
        spot_intent=spot.intent,
        has_fold_equity=spot.has_fold_equity,
        has_backup_equity=spot.has_backup_equity,
        spot_t1_note=spot.t1_note,
        spot_t2_note=spot.t2_note,
        spot_t3_note=spot.t3_note,
        spot_t4_note=spot.t4_note,
        hero_position=hero.position.value if hero.position else None,
        last_aggressor=villain.name if villain else None,
    )


def _pick_primary_villain(hand: Hand, hero: Player) -> Player | None:
    """Pick the most relevant opponent to compute equity against.

    Heuristic:
    - Last aggressor this street, if any, and they're a bot with archetype.
    - Else, the seated opponent left of the button (most active position).
    - Else, the first non-folded bot.
    """
    last_aggressor_id: str | None = None
    for ev in hand.history.events:
        if ev.kind == "player_action" and ev.data["action"] in ("raise", "bet"):
            last_aggressor_id = ev.data["player_id"]
    candidates = [p for p in hand.table.players if p.id != hero.id and not p.is_folded]
    if last_aggressor_id:
        match = next((p for p in candidates if p.id == last_aggressor_id), None)
        if match is not None:
            return match
    return candidates[0] if candidates else None


def _position_note(pos: Position | None, street: Street) -> str | None:
    if not pos:
        return None
    if street == Street.PREFLOP:
        if pos in (Position.UTG, Position.UTG1):
            return "You're under the gun — play tight, you have everyone left to act."
        if pos in (Position.BTN, Position.CO):
            return f"You're in {pos.value} — last to act postflop is a big edge."
        if pos in (Position.SB, Position.BB):
            return "Out of position post-flop is a structural disadvantage — defend tighter."
    if street != Street.PREFLOP:
        if pos in (Position.BTN, Position.CO):
            return "Position lets you control pot size and realize more equity."
        if pos in (Position.SB, Position.BB):
            return "OOP — you'll realize less of your raw equity. Be a bit cautious calling down."
    return None


def _blocker_note(hero_cards: list[int], arch: Archetype | None) -> str | None:
    if arch is None or not hero_cards:
        return None
    from the_felt.cards import rank_of
    ranks = [rank_of(c) for c in hero_cards]
    if 12 in ranks:  # Ace
        return "You hold an ace — that blocks AA combos in villain's range."
    if 11 in ranks:  # King
        return "You hold a king — modest blocker to KK and AK combos."
    return None


def _equity_realization_note(texture, pos: Position | None, street: Street) -> str | None:
    if texture is None or street == Street.PREFLOP:
        return None
    if texture.wet and pos in (Position.SB, Position.BB):
        return (
            "Wet board OOP — your raw equity overstates real-world EV; you can't "
            "always realize it before facing more bets."
        )
    if texture.nut_advantage_pfa > 0.3:
        return "High-card / dry board — favors the preflop aggressor's range."
    if texture.paired and not texture.high_card_present:
        return "Low paired board — flattens both ranges; small bets do most of the work."
    return None


def _range_type_note(texture, hand: Hand, hero: Player, arch: Archetype) -> str | None:
    if texture is None:
        return None
    if texture.nut_advantage_pfa < -0.2:
        return "Board favors caller — pfr's range is capped; consider polarized lines."
    if texture.wet:
        return "Wet board — large sizings build polarized ranges, small sizings stay merged."
    return None


def _exploit_note(arch: Archetype, equity: float, pot_odds_req: float) -> str | None:
    if arch.name == "Calling Station":
        if equity > 0.55:
            return "Calling station — value-bet thinner, never bluff. They call too much."
        return "Calling station ahead → don't bluff."
    if arch.name == "Nit":
        if equity < 0.4 and pot_odds_req > 0:
            return "Nit is rarely bluffing — when they raise the river, fold marginal hands."
        return "Nit folds too much to aggression — steal pots without showdown."
    if arch.name == "Maniac":
        return "Maniac bluffs constantly — widen calling range, slow-play big hands."
    if arch.name == "LAG":
        return "LAG plays wide & aggressive — call wider, but trap with monsters."
    if arch.name == "Whale":
        return "Whale calls everything — extract maximum value, never bluff."
    if arch.name == "TAG":
        return "TAG is balanced — exploits are thin; look for fold-equity spots in position."
    if arch.name == "GTO Reg":
        return "GTO reg — assume balanced ranges; play your own GTO baseline."
    return None


def _mixed_strategy_note(equity: float, edge: float, street: Street) -> str | None:
    if street == Street.RIVER and abs(edge) < 0.03:
        return "Close spot — solver would likely mix calls and folds here at some frequency."
    if 0.45 <= equity <= 0.55 and street in (Street.TURN, Street.RIVER):
        return "Coinflip equity — mixed strategies matter; you can't be exploited if you randomize."
    return None
