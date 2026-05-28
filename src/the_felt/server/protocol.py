"""WebSocket message contract (Pydantic-typed).

Inbound: messages from the browser client.
Outbound: messages from the server to the browser.

All messages share `{type, v, data}`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class WSMessage(BaseModel):
    type: str
    v: int = 1
    data: dict[str, Any] = Field(default_factory=dict)


# ---------- Inbound ----------


class JoinData(BaseModel):
    user_name: str = "You"
    seats: int = 6
    stack_bb: int = 100
    sb: int = 5
    bb: int = 10


class ActionData(BaseModel):
    hand_id: str
    seq: int
    action: Literal["fold", "check", "call", "bet", "raise", "all_in"]
    amount: int = 0  # raise-to / bet-to amount; 0 for fold/check/call


class NextHandData(BaseModel):
    pass


# ---------- Outbound ----------


class SeatInfo(BaseModel):
    seat: int
    id: str
    name: str
    stack: int
    position: str | None
    is_bot: bool
    archetype: str | None = None


class HandStartData(BaseModel):
    hand_id: str
    button_seat: int
    sb: int
    bb: int
    seats: list[SeatInfo]
    hero_seat: int
    hero_cards: list[str]
    hero_stack_before_blinds: int = 0  # baseline for computing net for the hand


class PlayerActionData(BaseModel):
    hand_id: str
    seq: int
    seat: int
    player_id: str
    street: str
    action: str
    amount: int
    stack_after: int
    committed_street_after: int
    pot_after: int


class BoardData(BaseModel):
    hand_id: str
    street: str
    new_cards: list[str]
    board: list[str]
    pot: int


class LegalActionsView(BaseModel):
    can_fold: bool
    can_check: bool
    can_call: bool
    call_amount: int
    can_bet: bool
    can_raise: bool
    min_raise_to: int
    max_raise_to: int


class ActionToActData(BaseModel):
    hand_id: str
    seq: int
    seat: int
    player_id: str
    street: str
    to_call: int
    pot: int
    legal: LegalActionsView


class CoachMath(BaseModel):
    equity: float
    pot_odds_required: float
    edge: float
    mdf: float
    alpha: float
    outs: int
    next_card_pct: float
    by_river_pct: float
    ev_by_action: dict[str, float]
    ev_labels: dict[str, str] = {}        # internal_key → display label
    verdict: str                           # internal_key of best action
    verdict_label: str = ""                # display label for the verdict
    verdict_button: str = ""               # which UI button id to highlight (fold|check|call|bet|raise|all_in)
    notes: list[str]


class CoachTipData(BaseModel):
    hand_id: str
    seq: int
    tier: int
    math: CoachMath


class HandEndData(BaseModel):
    hand_id: str
    winners: list[dict]
    showdown: list[dict]
    final_board: list[str]
    hero_net: int = 0          # net chip change for hero across the hand
    hero_stack_after: int = 0   # stack after the hand


class ErrorData(BaseModel):
    code: str
    message: str
