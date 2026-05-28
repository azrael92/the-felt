import random

from the_felt.cards import card
from the_felt.equity.monte_carlo import (
    equity_vs_hand,
    equity_vs_random,
    multiway_equity,
)


def test_aa_vs_kk():
    aa = [card("As"), card("Ah")]
    kk = [card("Ks"), card("Kh")]
    eq = equity_vs_hand(aa, kk, n=4000, rng=random.Random(0))
    assert 0.79 < eq < 0.84, f"AA vs KK ≈ 0.815; got {eq}"


def test_aks_vs_22_coinflip():
    aks = [card("As"), card("Ks")]
    tt = [card("2c"), card("2d")]
    eq = equity_vs_hand(aks, tt, n=4000, rng=random.Random(0))
    assert 0.46 < eq < 0.54, f"AKs vs 22 ≈ 0.50; got {eq}"


def test_aa_vs_random():
    aa = [card("As"), card("Ah")]
    eq = equity_vs_random(aa, n=4000, rng=random.Random(0))
    assert 0.82 < eq < 0.88, f"AA vs random ≈ 0.852; got {eq}"


def test_multiway_sums_to_one():
    h1 = [card("As"), card("Ah")]
    h2 = [card("Ks"), card("Kh")]
    h3 = [card("Qs"), card("Qh")]
    eqs = multiway_equity([h1, h2, h3], n=2000, rng=random.Random(0))
    assert abs(sum(eqs) - 1.0) < 1e-9
    assert eqs[0] > eqs[1] > eqs[2]
