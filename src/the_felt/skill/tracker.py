"""Glicko-2 rating tracker.

We treat each decision as a single micro-match against a virtual opponent
whose rating is the user's current rating plus a "stake" derived from the
delta_ev of the decision. An ideal-EV play is a draw (score = 0.5).

This isn't pure Glicko (which expects bulk match outcomes per period), but
it gives a smooth rating signal that responds to decision quality and has
a principled uncertainty (phi) which shrinks with practice and grows with
inactivity.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


# Glicko-2 constants (Mark Glickman 2013)
_TAU = 0.5
_EPSILON = 1e-6


@dataclass(slots=True)
class Glicko2Rating:
    mu: float = 1500.0     # rating
    phi: float = 350.0     # rating deviation
    sigma: float = 0.06    # volatility

    def to_glicko2(self) -> tuple[float, float]:
        return (self.mu - 1500.0) / 173.7178, self.phi / 173.7178


class GlickoTracker:
    """Single-decision Glicko-2 update."""

    @staticmethod
    def update(
        rating: Glicko2Rating,
        opponent_rating: float,
        opponent_phi: float,
        score: float,
    ) -> Glicko2Rating:
        """Update `rating` after one micro-match against an opponent.

        `score` in [0, 1]: 1 = win (ideal play), 0 = loss (terrible play), 0.5 = draw.
        Returns the new rating.
        """
        mu, phi = rating.to_glicko2()
        mu_j = (opponent_rating - 1500.0) / 173.7178
        phi_j = opponent_phi / 173.7178

        g_phi_j = 1.0 / math.sqrt(1.0 + 3.0 * phi_j * phi_j / (math.pi * math.pi))
        E = 1.0 / (1.0 + math.exp(-g_phi_j * (mu - mu_j)))

        v = 1.0 / (g_phi_j * g_phi_j * E * (1.0 - E))
        delta = v * g_phi_j * (score - E)

        # Volatility step (Glickman 2013 — illinois algorithm)
        a = math.log(rating.sigma * rating.sigma)

        def f(x: float) -> float:
            ex = math.exp(x)
            num = ex * (delta * delta - phi * phi - v - ex)
            den = 2.0 * (phi * phi + v + ex) ** 2
            return num / den - (x - a) / (_TAU * _TAU)

        A = a
        if delta * delta > phi * phi + v:
            B = math.log(delta * delta - phi * phi - v)
        else:
            k = 1
            while f(a - k * _TAU) < 0:
                k += 1
            B = a - k * _TAU
        fA = f(A)
        fB = f(B)
        for _ in range(50):
            if abs(B - A) < _EPSILON:
                break
            C = A + (A - B) * fA / (fB - fA)
            fC = f(C)
            if fC * fB <= 0:
                A, fA = B, fB
            else:
                fA = fA / 2.0
            B, fB = C, fC
        new_sigma = math.exp(A / 2.0)

        phi_star = math.sqrt(phi * phi + new_sigma * new_sigma)
        new_phi = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + 1.0 / v)
        new_mu = mu + new_phi * new_phi * g_phi_j * (score - E)

        return Glicko2Rating(
            mu=new_mu * 173.7178 + 1500.0,
            phi=max(30.0, new_phi * 173.7178),
            sigma=new_sigma,
        )

    @staticmethod
    def from_delta_ev(
        rating: Glicko2Rating,
        delta_ev_bb: float,
        opponent_offset: float = 0.0,
    ) -> Glicko2Rating:
        """Convenience: convert a `delta_ev_bb` (your action's EV minus ideal,
        normalized by BB) into a Glicko score and update.

        delta_ev_bb = 0 → score = 0.5 (draw vs equal opponent)
        delta_ev_bb > 0 → impossible by construction; clamped to 0
        delta_ev_bb < 0 → loss, magnitude scales score toward 0.

        We map score = sigmoid(delta_ev_bb / 1.5) clamped to (0.05, 0.5].
        """
        # tanh-based map: 0 → 0.5, -∞ → 0, +ε → 0.5 (ideal is best you can do).
        # Use a smoother conversion so big blunders push rating down meaningfully.
        s = 0.5 * (1.0 + math.tanh(delta_ev_bb / 1.5))
        s = max(0.05, min(0.5, s))
        return GlickoTracker.update(
            rating,
            opponent_rating=rating.mu + opponent_offset,
            opponent_phi=100.0,
            score=s,
        )
