"""Table and Player models."""

from __future__ import annotations

from dataclasses import dataclass, field

from the_felt.types import Position


# Position naming for N-handed tables, ordered from button clockwise.
# Index 0 = button, index 1 = SB, index 2 = BB, then continuing clockwise.
_POSITION_TABLES: dict[int, list[Position]] = {
    2: [Position.BTN, Position.BB],  # heads-up: BTN is SB
    3: [Position.BTN, Position.SB, Position.BB],
    4: [Position.BTN, Position.SB, Position.BB, Position.UTG],
    5: [Position.BTN, Position.SB, Position.BB, Position.UTG, Position.CO],
    6: [Position.BTN, Position.SB, Position.BB, Position.UTG, Position.HJ, Position.CO],
    7: [
        Position.BTN, Position.SB, Position.BB,
        Position.UTG, Position.MP, Position.HJ, Position.CO,
    ],
    8: [
        Position.BTN, Position.SB, Position.BB,
        Position.UTG, Position.UTG1, Position.MP, Position.HJ, Position.CO,
    ],
    9: [
        Position.BTN, Position.SB, Position.BB,
        Position.UTG, Position.UTG1, Position.MP, Position.LJ, Position.HJ, Position.CO,
    ],
    10: [
        Position.BTN, Position.SB, Position.BB,
        Position.UTG, Position.UTG1, Position.MP, Position.MP, Position.LJ, Position.HJ, Position.CO,
    ],
}


@dataclass(slots=True)
class Player:
    id: str
    name: str
    stack: int
    seat: int                  # seat index 0..N-1 (clockwise around table)
    is_bot: bool = True
    archetype_name: str | None = None  # only meaningful for bots
    # Live hand state (reset each hand):
    hole_cards: list[int] = field(default_factory=list)
    committed_total: int = 0   # total chips put in pot this hand (across streets)
    committed_street: int = 0  # chips put in on the current street
    is_folded: bool = False
    is_all_in: bool = False
    has_acted_this_street: bool = False
    position: Position | None = None  # set when a Hand starts

    def reset_for_hand(self) -> None:
        self.hole_cards = []
        self.committed_total = 0
        self.committed_street = 0
        self.is_folded = False
        self.is_all_in = False
        self.has_acted_this_street = False
        self.position = None


@dataclass(slots=True)
class Table:
    players: list[Player]
    button_seat: int  # which seat index has the dealer button
    sb: int
    bb: int
    ante: int = 0

    def num_players(self) -> int:
        return len(self.players)

    def num_active(self) -> int:
        """Players with chips (eligible to play next hand)."""
        return sum(1 for p in self.players if p.stack > 0)

    def assign_positions(self) -> None:
        """Stamp Position on each player relative to the current button.

        Positions count clockwise from the button. The seating order is
        `players` in seat-index order (seat 0, 1, 2, ...). For an N-handed
        table, the player at button_seat is BTN, button_seat+1 is SB, etc.
        """
        n = self.num_players()
        table = _POSITION_TABLES.get(n)
        if table is None:
            raise ValueError(f"Unsupported player count: {n}")
        for offset, pos in enumerate(table):
            seat = (self.button_seat + offset) % n
            self.players[seat].position = pos

    def advance_button(self) -> None:
        n = self.num_players()
        self.button_seat = (self.button_seat + 1) % n

    def seat(self, idx: int) -> Player:
        return self.players[idx % self.num_players()]

    def first_to_act_preflop_seat(self) -> int:
        """Seat that acts first preflop. For heads-up, the SB (=BTN) acts first."""
        n = self.num_players()
        if n == 2:
            return self.button_seat  # BTN/SB acts first preflop heads-up
        # UTG = button + 3
        return (self.button_seat + 3) % n

    def first_to_act_postflop_seat(self) -> int:
        """Seat that acts first postflop (first live player left of button)."""
        n = self.num_players()
        if n == 2:
            # Postflop heads-up: BB acts first
            return (self.button_seat + 1) % n
        # SB = button + 1
        return (self.button_seat + 1) % n
