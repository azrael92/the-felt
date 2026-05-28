"""One hand lifecycle: preflop → showdown.

The Hand is the central state machine. It's driven externally by:

    h = Hand(table, deck, hand_id)
    h.start()
    while not h.is_complete:
        req = h.next_action_request()
        action = pick_action(req)        # human via WS or bot policy
        h.apply(action)

All visible events (deal cards, post blinds, player actions, board updates,
winners) are appended to `h.history` and can be replayed for the UI.
"""

from __future__ import annotations

from dataclasses import dataclass

from the_felt.cards import Deck, to_str
from the_felt.engine.betting import (
    BettingState,
    apply_action,
    legal_actions_for,
    next_actor_seat,
    round_is_closed,
)
from the_felt.engine.history import HandHistory
from the_felt.engine.sidepots import Pot, build_pots, uncalled_refund
from the_felt.engine.table import Player, Table
from the_felt.eval import describe, rank_hand
from the_felt.types import Action, ActionType, LegalActions, Street


@dataclass(frozen=True, slots=True)
class ActionRequest:
    """Server emits this to whoever is to act."""

    seq: int
    player_id: str
    seat: int
    street: Street
    to_call: int
    min_raise_to: int
    max_raise_to: int
    pot: int
    legal: LegalActions


@dataclass(slots=True)
class HandResult:
    winners: list[dict]   # [{player_id, amount, hand_desc}]
    showdown: list[dict]  # [{player_id, cards, hand_desc}]
    final_board: list[int]


class Hand:
    def __init__(self, table: Table, deck: Deck, hand_id: str) -> None:
        self.table = table
        self.deck = deck
        self.hand_id = hand_id
        self.board: list[int] = []
        self.street = Street.PREFLOP
        self.history = HandHistory(hand_id=hand_id)
        self.state = BettingState()
        self.is_complete = False
        self.result: HandResult | None = None
        self._action_seq = 0
        self._started = False

    # ---------- lifecycle ----------

    def start(self) -> None:
        if self._started:
            raise RuntimeError("Hand already started")
        self._started = True

        # Reset players
        for p in self.table.players:
            p.reset_for_hand()
        # Filter out broke players? For Phase 1 we assume all have stacks.
        self.table.assign_positions()

        self.history.append(
            "hand_start",
            hand_id=self.hand_id,
            button_seat=self.table.button_seat,
            sb=self.table.sb,
            bb=self.table.bb,
            seats=[
                {
                    "seat": p.seat,
                    "id": p.id,
                    "name": p.name,
                    "stack": p.stack,
                    "position": p.position.value if p.position else None,
                    "is_bot": p.is_bot,
                }
                for p in self.table.players
            ],
        )

        # Deal hole cards (2 per player).
        for p in self.table.players:
            p.hole_cards = self.deck.deal(2)
            self.history.append(
                "deal_hole",
                seat=p.seat,
                player_id=p.id,
                cards=[to_str(c) for c in p.hole_cards],
            )

        # Post blinds.
        n = self.table.num_players()
        sb_seat = self.table.button_seat if n == 2 else (self.table.button_seat + 1) % n
        bb_seat = (sb_seat + 1) % n if n != 2 else (self.table.button_seat + 1) % n
        self._post_blind(sb_seat, self.table.sb, "sb")
        self._post_blind(bb_seat, self.table.bb, "bb")

        # Preflop betting state
        self.state.current_bet = self.table.bb
        self.state.last_raise_size = self.table.bb
        self.state.last_aggressor_seat = bb_seat
        # First actor: heads-up=BTN/SB, else=UTG (button + 3)
        first = self.table.first_to_act_preflop_seat()
        # Find actual seat (skip folded/all-in shouldn't apply here, but be safe)
        self.state.current_actor_seat = self._first_live_from(first)

        # BB's "option": even though they posted the BB, they haven't acted.
        # has_acted_this_street defaults to False — already correct.

    def _post_blind(self, seat: int, amount: int, label: str) -> None:
        p = self.table.players[seat]
        pay = min(amount, p.stack)
        p.stack -= pay
        p.committed_street = pay
        p.committed_total = pay
        if p.stack == 0:
            p.is_all_in = True
        self.history.append("post_blind", seat=seat, player_id=p.id, blind=label, amount=pay)

    def _first_live_from(self, seat: int) -> int:
        """Return `seat` if alive, else the next live (non-folded, non-all-in) seat clockwise."""
        n = self.table.num_players()
        for i in range(n):
            s = (seat + i) % n
            p = self.table.players[s]
            if not p.is_folded and not p.is_all_in:
                return s
        return seat  # shouldn't happen

    # ---------- driving the loop ----------

    def next_action_request(self) -> ActionRequest | None:
        if self.is_complete:
            return None
        seat = self.state.current_actor_seat
        if seat < 0:
            return None
        p = self.table.players[seat]
        legal = legal_actions_for(p, self.state, self.table.bb)
        self._action_seq += 1
        return ActionRequest(
            seq=self._action_seq,
            player_id=p.id,
            seat=seat,
            street=self.street,
            to_call=legal.call_amount,
            min_raise_to=legal.min_raise_to,
            max_raise_to=legal.max_raise_to,
            pot=self.pot_total(),
            legal=legal,
        )

    def apply(self, action: Action) -> None:
        if self.is_complete:
            raise RuntimeError("Hand is complete")
        seat = self.state.current_actor_seat
        if seat < 0:
            raise RuntimeError("No actor to apply action to")
        p = self.table.players[seat]
        apply_action(self.table, self.state, p, action, self.table.bb)
        self.history.append(
            "player_action",
            seat=seat,
            player_id=p.id,
            street=self.street.value,
            action=action.type.value,
            amount=action.amount if action.type in (
                ActionType.BET, ActionType.RAISE, ActionType.ALL_IN, ActionType.CALL
            ) else 0,
            stack_after=p.stack,
            committed_street_after=p.committed_street,
            pot_after=self.pot_total(),
        )

        # Check for hand-ending or street-ending conditions.
        live = [pl for pl in self.table.players if not pl.is_folded]
        if len(live) == 1:
            self._end_hand_no_showdown(live[0])
            return
        if round_is_closed(self.table, self.state):
            self._advance_street()
            return

        # Otherwise, advance to next actor.
        nxt = next_actor_seat(self.table, self.state)
        if nxt is None:
            # Round just closed
            self._advance_street()
            return
        self.state.current_actor_seat = nxt

    # ---------- street transitions ----------

    def _advance_street(self) -> None:
        # Move committed_street into committed_total accounting and reset.
        # (committed_street is already cumulative for the street; committed_total
        # is cumulative for the hand and was updated in apply_action.)
        for p in self.table.players:
            p.committed_street = 0
            p.has_acted_this_street = False
        self.state.reset_for_street()

        if self.street == Street.PREFLOP:
            self.street = Street.FLOP
            self._burn_and_deal(3)
        elif self.street == Street.FLOP:
            self.street = Street.TURN
            self._burn_and_deal(1)
        elif self.street == Street.TURN:
            self.street = Street.RIVER
            self._burn_and_deal(1)
        elif self.street == Street.RIVER:
            self._showdown()
            return
        else:
            self._showdown()
            return

        # Check if remaining live players can still act (i.e. at least 2 not all-in).
        live = [p for p in self.table.players if not p.is_folded]
        can_act = [p for p in live if not p.is_all_in]
        if len(can_act) < 2:
            # No more betting possible — run out the remaining streets and showdown.
            self._run_out_remaining_streets()
            self._showdown()
            return

        # Set first actor for the new street.
        first = self.table.first_to_act_postflop_seat()
        self.state.current_actor_seat = self._first_live_from(first)

    def _burn_and_deal(self, n: int) -> None:
        self.deck.deal(1)  # burn
        cards = self.deck.deal(n)
        self.board.extend(cards)
        self.history.append(
            "board",
            street=self.street.value,  # this is still the OLD street, but the event reflects new cards
            new_cards=[to_str(c) for c in cards],
            board=[to_str(c) for c in self.board],
            pot=self.pot_total(),
        )

    def _run_out_remaining_streets(self) -> None:
        """When everyone's all-in, just deal out the rest of the board."""
        if self.street == Street.FLOP:
            self.street = Street.TURN
            self._burn_and_deal(1)
        if self.street == Street.TURN:
            self.street = Street.RIVER
            self._burn_and_deal(1)

    # ---------- hand end ----------

    def _end_hand_no_showdown(self, winner: Player) -> None:
        # Build pots (winner is the only eligible)
        contribs = {p.id: p.committed_total for p in self.table.players}
        eligible = {winner.id}
        pots = build_pots(contribs, eligible)
        total = sum(pot.amount for pot in pots)
        # Add back any uncalled bet (folded around).
        refund = uncalled_refund(contribs, {winner.id})
        # Simpler approach for "everyone folded": winner gets everything.
        # Sum all contributions, give to winner.
        gross = sum(contribs.values())
        winner.stack += gross
        self.history.append(
            "hand_end_fold",
            winner_id=winner.id,
            winner_seat=winner.seat,
            amount=gross,
        )
        self.result = HandResult(
            winners=[{"player_id": winner.id, "amount": gross, "hand_desc": "uncontested"}],
            showdown=[],
            final_board=list(self.board),
        )
        self.is_complete = True
        self.state.current_actor_seat = -1

    def _showdown(self) -> None:
        live = [p for p in self.table.players if not p.is_folded]

        # Determine winners per pot.
        contribs = {p.id: p.committed_total for p in self.table.players}
        eligible_ids = {p.id for p in live}
        pots = build_pots(contribs, eligible_ids)

        # Refund any uncalled bet first.
        refund = uncalled_refund(contribs, eligible_ids)
        # Note: uncalled_refund logic for showdown is subtle; for Phase 1 we
        # rely on build_pots to allocate everything correctly when all
        # contributions get matched, which is the common showdown case.

        showdown_info: list[dict] = []
        ranks: dict[str, int] = {}
        for p in live:
            r = rank_hand(p.hole_cards, self.board)
            ranks[p.id] = r
            showdown_info.append({
                "player_id": p.id,
                "seat": p.seat,
                "cards": [to_str(c) for c in p.hole_cards],
                "hand_desc": describe(p.hole_cards, self.board),
            })

        winners_list: list[dict] = []
        # For each pot, find best rank (lowest) among eligible.
        for pot in pots:
            best_rank = min(ranks[pid] for pid in pot.eligible)
            winners = [pid for pid in pot.eligible if ranks[pid] == best_rank]
            share = pot.amount // len(winners)
            remainder = pot.amount - share * len(winners)
            for pid in winners:
                player = next(p for p in self.table.players if p.id == pid)
                amt = share + (remainder if pid == winners[0] else 0)
                player.stack += amt
                winners_list.append({
                    "player_id": pid,
                    "amount": amt,
                    "hand_desc": describe(player.hole_cards, self.board),
                    "pot_index": pots.index(pot),
                })

        self.history.append(
            "showdown",
            board=[to_str(c) for c in self.board],
            showdown=showdown_info,
            winners=winners_list,
        )
        self.result = HandResult(
            winners=winners_list,
            showdown=showdown_info,
            final_board=list(self.board),
        )
        self.is_complete = True
        self.state.current_actor_seat = -1

    # ---------- accessors ----------

    def pot_total(self) -> int:
        return sum(p.committed_total for p in self.table.players)
