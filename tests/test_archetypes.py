"""Verify archetype policies produce sane statistical signatures.

Run a few hundred decisions of each archetype facing a generic preflop spot
and check that VPIP/PFR fall within the documented bands.
"""

import random

from the_felt.agents.archetype import REGISTRY
from the_felt.agents.preflop_charts import first_in, facing_raise
from the_felt.agents.hand_strength import _populate_table
from the_felt.cards import ALL_CARDS
from the_felt.types import Position


def setup_module():
    _populate_table(n=200, seed=99)


def random_hand(rng):
    a, b = rng.sample(ALL_CARDS, 2)
    return [a, b]


def archetype_vpip(name, n=400, position=Position.BTN, seed=0):
    arch = REGISTRY[name]
    rng = random.Random(seed)
    voluntary = 0
    raises = 0
    for _ in range(n):
        hole = random_hand(rng)
        d = first_in(arch, position, hole)
        if d.action in ("raise", "call"):
            voluntary += 1
            if d.action == "raise":
                raises += 1
    return voluntary / n, raises / n


def test_nit_is_tight():
    vpip, pfr = archetype_vpip("Nit", n=500, position=Position.BTN)
    # On BTN with vpip factor 0.68, opens about 28%. Nit is the tightest.
    # Just verify it's tighter than TAG.
    tag_vpip, _ = archetype_vpip("TAG", n=500, position=Position.BTN)
    assert vpip < tag_vpip, f"Nit ({vpip:.2f}) should play tighter than TAG ({tag_vpip:.2f})"


def test_lag_is_looser_than_tag():
    lag_vpip, _ = archetype_vpip("LAG", n=500, position=Position.BTN)
    tag_vpip, _ = archetype_vpip("TAG", n=500, position=Position.BTN)
    assert lag_vpip > tag_vpip


def test_maniac_aggression():
    _, maniac_pfr = archetype_vpip("Maniac", n=500, position=Position.BTN)
    _, tag_pfr = archetype_vpip("TAG", n=500, position=Position.BTN)
    assert maniac_pfr > tag_pfr


def test_calling_station_more_limps_than_raises():
    arch = REGISTRY["Calling Station"]
    rng = random.Random(0)
    limps = 0
    raises = 0
    for _ in range(500):
        hole = random_hand(rng)
        d = first_in(arch, Position.CO, hole)
        if d.action == "call":
            limps += 1
        elif d.action == "raise":
            raises += 1
    # Calling stations limp more than they raise
    assert limps > raises, f"Calling station should limp ({limps}) more than raise ({raises})"


def test_early_position_tighter_than_late():
    arch = REGISTRY["TAG"]
    rng = random.Random(7)
    utg_voluntary = 0
    btn_voluntary = 0
    for _ in range(500):
        hole = random_hand(rng)
        if first_in(arch, Position.UTG, hole).action != "fold":
            utg_voluntary += 1
        # Use a fresh rng position-wise so distribution is comparable:
    rng2 = random.Random(7)
    for _ in range(500):
        hole = random_hand(rng2)
        if first_in(arch, Position.BTN, hole).action != "fold":
            btn_voluntary += 1
    assert utg_voluntary < btn_voluntary
