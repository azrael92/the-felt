"""Archetype configuration. Phase 1 uses defaults from the research bands."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Archetype:
    """Statistical signature of one playing style.

    All percentages are 0..1 fractions. `noise_sigma` controls jitter applied
    to decision frequencies; higher = more random play.
    """

    name: str
    # Preflop stats (target bands from research)
    vpip: float = 0.20
    pfr: float = 0.16
    three_bet: float = 0.06
    fold_to_3bet: float = 0.65
    # Postflop stats
    afq: float = 0.45            # aggression frequency
    cbet_freq: float = 0.65
    bluff_freq: float = 0.10     # how often we bluff with air
    overbet_freq: float = 0.05
    # Style modifiers
    aggression_mult: float = 1.0
    tightness_mult: float = 1.0
    noise_sigma: float = 0.05    # action noise


# Built-in archetypes (Phase 1 uses TAG only; Phase 2 wires the rest)
NIT = Archetype(
    name="Nit", vpip=0.13, pfr=0.04, three_bet=0.02, fold_to_3bet=0.80,
    afq=0.25, cbet_freq=0.45, bluff_freq=0.03, overbet_freq=0.0,
    aggression_mult=0.5, tightness_mult=1.8, noise_sigma=0.03,
)
TAG = Archetype(
    name="TAG", vpip=0.19, pfr=0.16, three_bet=0.06, fold_to_3bet=0.65,
    afq=0.45, cbet_freq=0.65, bluff_freq=0.12, overbet_freq=0.04,
    aggression_mult=1.0, tightness_mult=1.0, noise_sigma=0.05,
)
CALLING_STATION = Archetype(
    name="Calling Station", vpip=0.40, pfr=0.07, three_bet=0.02, fold_to_3bet=0.40,
    afq=0.18, cbet_freq=0.50, bluff_freq=0.05, overbet_freq=0.0,
    aggression_mult=0.4, tightness_mult=0.5, noise_sigma=0.10,
)
LAG = Archetype(
    name="LAG", vpip=0.32, pfr=0.26, three_bet=0.10, fold_to_3bet=0.50,
    afq=0.58, cbet_freq=0.75, bluff_freq=0.22, overbet_freq=0.08,
    aggression_mult=1.5, tightness_mult=0.7, noise_sigma=0.07,
)
MANIAC = Archetype(
    name="Maniac", vpip=0.50, pfr=0.40, three_bet=0.15, fold_to_3bet=0.30,
    afq=0.70, cbet_freq=0.85, bluff_freq=0.35, overbet_freq=0.20,
    aggression_mult=2.0, tightness_mult=0.4, noise_sigma=0.12,
)
WHALE = Archetype(
    name="Whale", vpip=0.48, pfr=0.06, three_bet=0.01, fold_to_3bet=0.25,
    afq=0.12, cbet_freq=0.40, bluff_freq=0.02, overbet_freq=0.0,
    aggression_mult=0.3, tightness_mult=0.3, noise_sigma=0.15,
)
GTO_REG = Archetype(
    name="GTO Reg", vpip=0.24, pfr=0.20, three_bet=0.08, fold_to_3bet=0.60,
    afq=0.50, cbet_freq=0.60, bluff_freq=0.30, overbet_freq=0.06,
    aggression_mult=1.0, tightness_mult=1.0, noise_sigma=0.02,
)


REGISTRY: dict[str, Archetype] = {
    "Nit": NIT,
    "TAG": TAG,
    "Calling Station": CALLING_STATION,
    "LAG": LAG,
    "Maniac": MANIAC,
    "Whale": WHALE,
    "GTO Reg": GTO_REG,
}
