# the_felt

Texas Hold'em probability trainer. Play against rule-based AI opponents with distinct poker archetypes. A coach shows you the probabilities turn-by-turn (equity, pot odds, MDF, EV) and the opponents get tougher as your skill rating climbs.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # fill in ANTHROPIC_API_KEY for Phase 2+ features
uvicorn the_felt.server.app:app --reload
```

Open <http://localhost:8000>.

## Layout

```
src/the_felt/
  cards.py, eval.py        # Cards, deck, hand evaluation (Treys)
  engine/                  # Table, Hand, betting rounds, side pots, history
  equity/                  # Monte Carlo equity calculation
  ranges/                  # Range model, combo enumeration, preflop charts
  probability/             # Pot odds, MDF, alpha, EV, outs, blockers
  agents/                  # Archetype-driven rule-based bots
  coach/                   # Decision analyzer + tiered explanations
  skill/                   # Glicko-2 rating, decision evaluator, difficulty adapter
  server/                  # FastAPI + WebSocket
  static/                  # Frontend (vanilla JS + SVG table)
```

See `/Users/rishi/.claude/plans/fuzzy-growing-thompson.md` for the full plan.
