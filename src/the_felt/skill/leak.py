"""Leak detection: find the user's most common decision mistake in recent play."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


@dataclass(slots=True)
class LeakReport:
    """Aggregated picture of the user's recent leaks."""

    top_leak: str | None
    top_leak_count: int
    total_decisions: int
    blunder_rate: float       # blunders / total
    leak_rate: float          # (minor_leak + blunder) / total
    counts: dict[str, int]    # leak_tag → count


def detect_leaks(
    decisions: list[dict],
    max_recent: int = 100,
) -> LeakReport:
    """Take the most recent `max_recent` decisions and summarize leaks.

    Each `decisions` entry is a dict with keys: `bucket`, `leak_tag`.
    """
    recent = decisions[-max_recent:] if len(decisions) > max_recent else list(decisions)
    if not recent:
        return LeakReport(None, 0, 0, 0.0, 0.0, {})

    counts: Counter[str] = Counter()
    blunders = 0
    leaks = 0
    for d in recent:
        if d.get("bucket") == "blunder":
            blunders += 1
        if d.get("bucket") in ("minor_leak", "blunder"):
            leaks += 1
            tag = d.get("leak_tag")
            if tag:
                counts[tag] += 1

    top = counts.most_common(1)
    top_tag = top[0][0] if top else None
    top_n = top[0][1] if top else 0
    return LeakReport(
        top_leak=top_tag,
        top_leak_count=top_n,
        total_decisions=len(recent),
        blunder_rate=blunders / len(recent),
        leak_rate=leaks / len(recent),
        counts=dict(counts),
    )
