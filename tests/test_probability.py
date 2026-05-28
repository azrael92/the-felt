from the_felt.probability.ev import ev_call, ev_fold, ev_raise
from the_felt.probability.mdf import alpha, mdf
from the_felt.probability.pot_odds import pot_odds


def test_pot_odds_basics():
    assert pot_odds(0, 100) == 0.0
    # $20 to call into $80 pot → 20/100 = 0.20
    assert abs(pot_odds(20, 80) - 0.20) < 1e-9


def test_mdf_and_alpha():
    # Half pot bet
    assert abs(mdf(100, 50) - (100 / 150)) < 1e-9
    assert abs(alpha(50, 100) - (50 / 150)) < 1e-9
    # Sum of MDF + alpha = 1 (definitionally)
    assert abs(mdf(100, 50) + alpha(50, 100) - 1.0) < 1e-9


def test_ev_call_break_even_at_pot_odds():
    # 25% equity, 25% pot odds → EV ≈ 0
    pot, to_call = 75, 25
    ev = ev_call(equity=0.25, to_call=to_call, pot=pot)
    assert abs(ev) < 0.01


def test_ev_call_positive_when_equity_exceeds_pot_odds():
    pot, to_call = 75, 25
    ev = ev_call(equity=0.5, to_call=to_call, pot=pot)
    assert ev > 0


def test_ev_fold_is_zero():
    assert ev_fold() == 0


def test_ev_raise_positive_with_high_fold_equity():
    ev = ev_raise(
        equity_when_called=0.4,
        raise_amount=50,
        pot_before_raise=20,
        to_call_before=0,
        fold_probability=0.9,
    )
    # Mostly we win the existing pot
    assert ev > 0
