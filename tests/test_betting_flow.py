"""Integration tests for the engine via simulated hands."""

from the_felt.cards import Deck
from the_felt.engine.hand import Hand
from the_felt.engine.table import Player, Table
from the_felt.types import Action, ActionType


def make_table(n: int, stack: int = 1000) -> Table:
    players = [Player(id=f"p{i}", name=f"P{i}", seat=i, stack=stack) for i in range(n)]
    return Table(players=players, button_seat=0, sb=5, bb=10)


def test_blinds_posted_correctly_6max():
    table = make_table(6)
    deck = Deck(seed=1)
    hand = Hand(table, deck, "h1")
    hand.start()
    # SB at seat 1, BB at seat 2
    assert table.players[1].committed_total == 5
    assert table.players[2].committed_total == 10
    # First to act preflop: UTG at seat 3
    req = hand.next_action_request()
    assert req is not None
    assert req.seat == 3


def test_heads_up_btn_acts_first_preflop():
    table = make_table(2)
    deck = Deck(seed=2)
    hand = Hand(table, deck, "h1")
    hand.start()
    # In HU, BTN posts SB and acts first preflop
    assert table.players[0].committed_total == 5
    assert table.players[1].committed_total == 10
    req = hand.next_action_request()
    assert req.seat == 0


def test_full_hand_check_down_returns_pot_to_winner():
    table = make_table(2, stack=1000)
    deck = Deck(seed=42)
    hand = Hand(table, deck, "h1")
    hand.start()
    # BTN limps (call to 10), BB checks the option
    hand.apply(Action(ActionType.CALL, 10))
    hand.apply(Action(ActionType.CHECK))
    # Flop check-check
    hand.apply(Action(ActionType.CHECK))
    hand.apply(Action(ActionType.CHECK))
    # Turn check-check
    hand.apply(Action(ActionType.CHECK))
    hand.apply(Action(ActionType.CHECK))
    # River check-check → showdown
    hand.apply(Action(ActionType.CHECK))
    hand.apply(Action(ActionType.CHECK))
    assert hand.is_complete
    # Total chips before == after (excluding rake, which we have none)
    total_after = sum(p.stack for p in table.players)
    assert total_after == 2000


def test_fold_to_raise_awards_pot():
    table = make_table(2, stack=1000)
    deck = Deck(seed=99)
    hand = Hand(table, deck, "h1")
    hand.start()
    # BTN raises to 30, BB folds
    hand.apply(Action(ActionType.RAISE, 30))
    hand.apply(Action(ActionType.FOLD))
    assert hand.is_complete
    # BTN should win the pot (their bet + BB's 10 blind)
    btn = table.players[0]
    bb = table.players[1]
    assert btn.stack == 1010  # gained the 10 from BB
    assert bb.stack == 990


def test_min_raise_enforced():
    table = make_table(2, stack=1000)
    deck = Deck(seed=7)
    hand = Hand(table, deck, "h1")
    hand.start()
    # BTN tries to raise to 15 (below min raise of 20)
    try:
        hand.apply(Action(ActionType.RAISE, 15))
        raised = False
    except ValueError:
        raised = True
    assert raised, "min raise should be enforced"


def test_six_max_hand_completes():
    table = make_table(6, stack=1000)
    deck = Deck(seed=123)
    hand = Hand(table, deck, "h6")
    hand.start()
    # UTG opens to 30, everyone folds to BB who folds → SB wins (the raise was uncontested-ish)
    # Actually UTG raises, MP/CO/BTN/SB/BB all fold.
    hand.apply(Action(ActionType.RAISE, 30))   # UTG
    for _ in range(5):
        req = hand.next_action_request()
        assert req is not None
        hand.apply(Action(ActionType.FOLD))
    assert hand.is_complete
    # UTG should have won the blinds (5 + 10 = 15)
    utg = table.players[3]
    assert utg.stack == 1015
