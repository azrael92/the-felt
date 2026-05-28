"""Append-only hand history. Every observable event is logged for replay/teaching."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class HistoryEvent:
    kind: str
    data: dict[str, Any]
    seq: int


@dataclass(slots=True)
class HandHistory:
    hand_id: str
    events: list[HistoryEvent] = field(default_factory=list)
    _next_seq: int = 0

    def append(self, kind: str, **data: Any) -> HistoryEvent:
        ev = HistoryEvent(kind=kind, data=data, seq=self._next_seq)
        self.events.append(ev)
        self._next_seq += 1
        return ev

    def to_dict(self) -> dict[str, Any]:
        return {
            "hand_id": self.hand_id,
            "events": [
                {"seq": e.seq, "kind": e.kind, "data": e.data} for e in self.events
            ],
        }
