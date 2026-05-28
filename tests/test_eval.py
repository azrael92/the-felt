from the_felt.cards import card
from the_felt.eval import describe, rank_hand


def test_royal_flush_is_best():
    hole = [card("As"), card("Ks")]
    board = [card("Qs"), card("Js"), card("Ts")]
    rank = rank_hand(hole, board)
    assert rank == 1, "Royal flush should be rank 1 in Treys"
    assert describe(hole, board) == "Royal Flush"


def test_pair_beats_high_card():
    pair = [card("As"), card("Ah")]
    high = [card("Kh"), card("Qc")]
    board = [card("2c"), card("7d"), card("9s")]
    assert rank_hand(pair, board) < rank_hand(high, board)


def test_two_pair_beats_pair():
    two_pair = [card("As"), card("Kc")]   # AAKK
    one_pair = [card("Qs"), card("Qd")]   # QQ + kickers
    board = [card("Ac"), card("Kh"), card("2d"), card("3s"), card("4c")]
    assert rank_hand(two_pair, board) < rank_hand(one_pair, board)


def test_describe_incomplete():
    assert describe([card("As")], []) == "incomplete"
