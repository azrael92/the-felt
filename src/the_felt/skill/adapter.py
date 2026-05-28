"""Difficulty adapter: pick an opponent seating + parameter overrides for a given rating.

Strategy:
- Below 1200: lots of Stations / Whales / one Nit. Loose, exploitable. Easy.
- 1200-1600: balanced TAG / LAG mix.
- 1600-2000: + semi-GTO regs, tighter ranges, mixed strategies enabled.
- > 2000: GTO regs that adapt to the user's leak (fed in by `leak_tag`).

Archetype noise (jitter) is decreased as rating goes up, so opponents make
fewer "wrong" moves at higher tiers.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from the_felt.agents.archetype import REGISTRY, Archetype


@dataclass(slots=True)
class DifficultyProfile:
    """A recipe for seating archetypes around the table."""

    rating_band: str
    archetype_pool: list[str]          # names from REGISTRY (drawn with replacement)
    noise_multiplier: float = 1.0      # multiplier on archetype.noise_sigma
    aggression_multiplier: float = 1.0  # multiplier on archetype.aggression_mult
    tightness_multiplier: float = 1.0   # multiplier on archetype.tightness_mult
    exploit_user: bool = False         # if True, regs adapt to user's leak

    def assign_archetypes(self, n_bots: int, rng: random.Random) -> list[Archetype]:
        """Pick `n_bots` archetypes from the pool, applying multipliers."""
        chosen_names = [self.archetype_pool[rng.randrange(len(self.archetype_pool))] for _ in range(n_bots)]
        out: list[Archetype] = []
        for name in chosen_names:
            base = REGISTRY[name]
            adjusted = Archetype(
                name=base.name,
                vpip=_clamp(base.vpip * (1.0 / max(0.5, self.tightness_multiplier)), 0.05, 0.95),
                pfr=_clamp(base.pfr * self.aggression_multiplier, 0.0, 0.6),
                three_bet=_clamp(base.three_bet * self.aggression_multiplier, 0.0, 0.25),
                fold_to_3bet=base.fold_to_3bet,
                afq=_clamp(base.afq * self.aggression_multiplier, 0.0, 0.95),
                cbet_freq=_clamp(base.cbet_freq * self.aggression_multiplier, 0.0, 0.95),
                bluff_freq=_clamp(base.bluff_freq * self.aggression_multiplier, 0.0, 0.5),
                overbet_freq=base.overbet_freq,
                aggression_mult=base.aggression_mult * self.aggression_multiplier,
                tightness_mult=base.tightness_mult * self.tightness_multiplier,
                noise_sigma=_clamp(base.noise_sigma * self.noise_multiplier, 0.01, 0.5),
            )
            out.append(adjusted)
        return out


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def difficulty_for(rating_mu: float, leak_tag: str | None = None) -> DifficultyProfile:
    if rating_mu < 1200:
        return DifficultyProfile(
            rating_band="beginner",
            archetype_pool=["Calling Station", "Whale", "Calling Station", "Whale", "Nit"],
            noise_multiplier=1.6,
            tightness_multiplier=0.7,   # opponents are loose
            aggression_multiplier=0.9,
        )
    if rating_mu < 1600:
        return DifficultyProfile(
            rating_band="intermediate",
            archetype_pool=["TAG", "TAG", "LAG", "Calling Station", "Nit"],
            noise_multiplier=1.0,
        )
    if rating_mu < 2000:
        return DifficultyProfile(
            rating_band="advanced",
            archetype_pool=["TAG", "LAG", "GTO Reg", "TAG", "Maniac"],
            noise_multiplier=0.7,
            tightness_multiplier=1.1,
        )
    # Expert: GTO regs that exploit the user's leak
    profile = DifficultyProfile(
        rating_band="expert",
        archetype_pool=["GTO Reg", "GTO Reg", "GTO Reg", "TAG", "LAG"],
        noise_multiplier=0.4,
        exploit_user=True,
    )
    # Steer aggression based on user's leak
    if leak_tag == "fold_too_much":
        profile.aggression_multiplier = 1.4   # regs barrel more vs over-folder
    elif leak_tag == "call_too_much":
        profile.aggression_multiplier = 0.8   # regs cut down bluffs vs station
    elif leak_tag == "bluff_too_much":
        profile.aggression_multiplier = 1.1   # regs call lighter vs over-bluffer
    elif leak_tag == "under_aggression":
        profile.aggression_multiplier = 0.9
    return profile
