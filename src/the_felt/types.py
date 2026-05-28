"""Shared types, enums, and constants."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import NewType


Chips = NewType("Chips", int)


class Street(str, Enum):
    PREFLOP = "preflop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"


class Position(str, Enum):
    # Positions named relative to the button. SB/BB are blinds.
    SB = "SB"
    BB = "BB"
    UTG = "UTG"
    UTG1 = "UTG+1"
    MP = "MP"
    LJ = "LJ"
    HJ = "HJ"
    CO = "CO"
    BTN = "BTN"
    # For heads-up the button is also SB.


class ActionType(str, Enum):
    FOLD = "fold"
    CHECK = "check"
    CALL = "call"
    BET = "bet"
    RAISE = "raise"
    ALL_IN = "all_in"


@dataclass(frozen=True, slots=True)
class Action:
    """One action taken by one player."""

    type: ActionType
    amount: int = 0  # Total chips committed by THIS action (above what was already in)

    def __repr__(self) -> str:
        if self.type in (ActionType.FOLD, ActionType.CHECK):
            return self.type.value
        return f"{self.type.value} {self.amount}"


@dataclass(frozen=True, slots=True)
class LegalActions:
    """What the current to-act player may legally do."""

    can_fold: bool
    can_check: bool
    can_call: bool
    call_amount: int  # chips needed to call (0 if can_check)
    can_bet: bool
    can_raise: bool
    min_raise_to: int  # raise-to floor (total street chip target)
    max_raise_to: int  # raise-to ceiling (effective all-in)


# Total combos in Hold'em deck: C(52, 2) = 1326
TOTAL_COMBOS = 1326

# Postflop action grid sizing fractions of pot, plus actions.
ACTION_GRID = [
    "check",
    "call",
    "bet_33",
    "bet_66",
    "bet_100",
    "overbet_150",
    "raise_2.5x",
    "raise_3.5x",
    "fold",
    "all_in",
]
