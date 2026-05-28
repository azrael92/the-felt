"""Expected value calculations for poker decisions."""

from __future__ import annotations


def ev_fold() -> float:
    """Folding has 0 EV — we just give up any chips already in the pot."""
    return 0.0


def ev_call(equity: float, to_call: int, pot: int) -> float:
    """EV of a call.

    `pot` is the size of the pot RIGHT NOW (after the bet we're facing,
    before our call). `to_call` is what we'd add.

    On equity fraction of showdowns we win `pot`. Otherwise we lose `to_call`.

    EV = equity * pot - (1 - equity) * to_call

    Break-even when equity == to_call / (pot + to_call) — i.e. pot odds.
    """
    return equity * pot - (1.0 - equity) * to_call


def ev_raise(
    equity_when_called: float,
    raise_amount: int,
    pot_before_raise: int,
    to_call_before: int,
    fold_probability: float,
) -> float:
    """EV of a raise (or bet).

    `raise_amount` is the chips we put in beyond what was already in pot for us
      (i.e. our additional commitment from this action).
    `pot_before_raise` is pot size BEFORE we put `raise_amount` in.
    `to_call_before` is what we faced (0 for a fresh bet).
    `fold_probability` is the chance opponents fold to our raise.

    Fold branch: we win `pot_before_raise`.
    Called branch: equity_when_called * (final_pot) - our_commitment.
      final_pot = pot_before_raise + raise_amount + (raise_amount - to_call_before)
                = pot_before_raise + 2*raise_amount - to_call_before
      our_commitment = raise_amount

    Note: this is a heads-up approximation; multiway gets messier.
    """
    fold_branch = fold_probability * pot_before_raise
    final_pot = pot_before_raise + 2 * raise_amount - to_call_before
    called_branch = (1 - fold_probability) * (
        equity_when_called * final_pot - raise_amount
    )
    return fold_branch + called_branch
