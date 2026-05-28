from the_felt.engine.sidepots import build_pots


def test_simple_two_way():
    # Both players matched at 100
    pots = build_pots({"a": 100, "b": 100}, eligible={"a", "b"})
    assert len(pots) == 1
    assert pots[0].amount == 200
    assert set(pots[0].eligible) == {"a", "b"}


def test_one_all_in_three_way():
    # a all-in for 50, b and c continue to 200
    contribs = {"a": 50, "b": 200, "c": 200}
    pots = build_pots(contribs, eligible={"a", "b", "c"})
    # main pot: 3 * 50 = 150 (all eligible)
    # side pot: 2 * 150 = 300 (only b, c)
    assert pots[0].amount == 150
    assert set(pots[0].eligible) == {"a", "b", "c"}
    assert pots[1].amount == 300
    assert set(pots[1].eligible) == {"b", "c"}


def test_folded_dead_money_goes_to_main():
    # a folded after putting in 30; b and c go to showdown for 100 each
    contribs = {"a": 30, "b": 100, "c": 100}
    pots = build_pots(contribs, eligible={"b", "c"})
    # All three contributed up to 30 → main has 90 from contributions
    # but only b/c eligible. Then b/c each add 70 → side has 140.
    # Total should equal sum of contribs (230).
    total = sum(p.amount for p in pots)
    assert total == 230
    for pot in pots:
        assert set(pot.eligible).issubset({"b", "c"})


def test_three_different_all_in_levels():
    # Stacks: a=30, b=80, c=200. All all-in. (eligible = all three)
    contribs = {"a": 30, "b": 80, "c": 200}
    pots = build_pots(contribs, eligible={"a", "b", "c"})
    # main: 3*30 = 90; side1: 2*50 = 100; side2: c's uncalled 120
    # But uncalled bets get refunded outside build_pots; build_pots
    # will create a tier at 200 with only c eligible (= 120).
    assert pots[0].amount == 90
    assert set(pots[0].eligible) == {"a", "b", "c"}
    assert pots[1].amount == 100
    assert set(pots[1].eligible) == {"b", "c"}
    assert pots[2].amount == 120
    assert set(pots[2].eligible) == {"c"}
