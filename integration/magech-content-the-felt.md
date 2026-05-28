---
title: The Felt
slug: the_felt
section: projects
status: published
authored_by: rishi
reviewed_by: rishi
created: 2026-05-26T00:00:00Z
published: 2026-05-26T00:00:00Z
revision: 1
tags: [poker, probability, pedagogy, training, anthropic-sdk, websocket]
project_name: The Felt
project_status: active
tagline: "A Texas Hold'em probability trainer built like a flight simulator — unlocks one control axis at a time until outs, pot odds, EV, ranges and exploits are reflexes"
problem: "Most poker trainers either teach abstract math without applying it or simulate full games without scaffolding the math. The student is either bored or overwhelmed. Neither produces a player who can compute and decide in 5 seconds at the table."
approach: "Mirror how helicopter pilots are taught: control one axis while the simulator handles the others, then unlock the next axis. Eight stages — hand reading → outs → rule of 2&4 → pot odds → EV → position → ranges → archetypes → exploits — each gated by an adaptive-frequency quiz that tightens when the student slips and relaxes when they're consistent. Live decisions count toward mastery so the simulator doubles as a practice ground."
stack: ["Python 3.12", "FastAPI", "WebSocket", "Treys", "NumPy", "Anthropic SDK", "SQLite", "Vanilla JS + SVG", "Docker", "Cloudflare Tunnel"]
links:
  - label: "Live demo"
    url: "https://felt.magech.ai"
  - label: "GitHub"
    url: "https://github.com/azrael92/the-felt"
metrics:
  - label: "Training stages"
    value: "8 (hand reading → exploits + free flight)"
  - label: "Drill kinds"
    value: "15+ across math, bluffing, archetypes"
  - label: "Test coverage"
    value: "83 tests · engine + curriculum + persistence"
postmortem_notes: null
---

The demo is live. Play through stage 1 and see if it teaches differently than the trainers you've tried.

<iframe
  src="https://felt.magech.ai/"
  loading="lazy"
  style="width:100%; aspect-ratio:16/10; min-height:640px; border:1px solid var(--ink-rule); border-radius:8px; background:#12100e; margin: 1.5rem 0;"
  sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox"
  title="The Felt live demo"
  allow="clipboard-write"
></iframe>

## The pedagogy problem

Poker is taught in two broken ways. Either someone explains pot odds and EV abstractly without putting you in the seat, and you can't translate it under pressure. Or you sit at a table and the math is hidden inside an opaque "is this a good play?" feeling that takes years to calibrate.

Helicopter pilots aren't taught either way. There are three control axes — cyclic, collective, anti-torque — and operating all three is nothing like operating any one of them. So the instructor takes two axes, the student takes one, until the first one is reflexive. Then they swap. Then they swap again. The mental load expands one axis at a time.

The Felt is that, for Hold'em.

## Eight axes, unlocked one at a time

The student starts on stage 1 with one job: identify the hand class on the board. Outs, equity, pot odds, EV, position, ranges, archetype, recommended action — all handled by the trainer and shown live in a "we're handling these for you" panel. The student answers the one question on every meaningful turn.

When they answer correctly 5 times in a row, the modal stops firing on every turn — every 2-3, then every 3-4. When they make a mistake the frequency snaps back to every turn. After 5 clean hands at the relaxed cadence the trainer offers to graduate them to stage 2.

Stage 2 adds outs counting. The handled-for-you panel shrinks. The same quiz-and-relax loop runs, now with two axes — hand reading is still tested implicitly through play (every great-or-better live decision counts as a synthetic drill credit toward the relevant module), but the conscious work is on outs.

Each stage adds exactly one new control axis:

| # | Stage | The new control |
|---|---|---|
| 1 | Read your hand | Hand class on the board |
| 2 | Count your outs | Cards that lift you to a stronger hand class |
| 3 | Compute pot odds | The price-to-call formula and the should-I-continue threshold |
| 4 | EV in chips | The expected-chips formula — math in chips, not in percentages |
| 5 | Position | IP vs OOP and how that adjusts your continue range |
| 6 | Ranges | Estimating villain's distribution from their actions |
| 7 | Archetype | Reading the player type from VPIP/PFR/AFq signatures |
| 8 | Exploits + free flight | Opponent-specific counter-strategies, with no recommendation shown |

By stage 8 the trainer has stopped handling anything. The student is flying solo against rule-based bots that play one of seven archetypes (Nit, TAG, LAG, Calling Station, Maniac, Whale, GTO Reg), tuned by a difficulty adapter that gets harder as the rating climbs and exploits the user's known leak history at the top band.

## The "this isn't really math" critique that drove a rewrite

A first cut of the app showed pot odds as a percentage in a side panel — "you need 28% equity to call" — and that was the entire teaching. The user (Rishi, testing it) flagged the obvious: that's just a number, not math. There's no formula visible, no derivation, no breakdown of which outs were actually being counted.

The fix was a step-by-step derivation panel that shows the formula symbolically, then with the actual numbers plugged in, then the result, then a one-line gloss of why it matters. For outs questions a wrong answer doesn't say "the correct answer is 11." It says: **you said 3, the answer is 11 — here are the 2 outs to Three of a Kind (6h 6d) plus the 9 outs to Two Pair (2s 2d 2c 5s 5h 5d Js Jh Jc).** The student sees exactly what they missed.

Each stage also gets a multi-step walkthrough overlay the first time the user enters it: concept definition, formula, common values to memorize, and a worked example with rendered cards. Stage 2's flush-draw example shows 9♠ 8♠ on K♠ 7♥ 4♠ with the answer "13 spades − 4 visible = 9 outs" — same wording the in-hand math panel uses, so drill prose and live coach prose are identical.

## Adaptive frequency, not a fixed schedule

A naive trainer asks the same question every turn forever. A slightly less naive one quizzes you N times then stops. Both are wrong. The right model is: tighten when the student struggles, relax when they're consistent, and snap back to tight at the first mistake.

The frequency table:

| Correct streak | Quiz fires on |
|---|---|
| 0–4 | every meaningful turn |
| 5–9 | ~⅔ of turns |
| 10–14 | ~⅓ of turns |
| 15+ | ~⅕ of turns |
| any wrong answer | resets to every turn |

Plus: trivial spots (auto-fold pre-flop with 72o, check on a river with no draw) skip the quiz entirely so play doesn't drag. The student is only quizzed on spots where the concept actually applies.

A "good-decision streak" counter in the header tracks consecutive non-blunder live decisions. Each hand can credit the streak at most once (anti-gaming guard so fold-spamming pre-flop doesn't ratchet it up infinitely).

## What's in the box

- **A real game engine.** 6-max No-Limit Hold'em with side pots, position rotation, bot policies parameterized by archetype (VPIP / PFR / AFq / c-bet% / bluff%). Built on Treys for hand evaluation, Monte Carlo for equity (10k samples for the coach, 1k for bots).
- **A real coach.** Decision context computes equity vs estimated villain ranges (not vs random), pot odds, MDF, alpha, EV for every legal action, outs with discounted-outs accounting, blocker effects. Tier-filtered explanations from beginner plain English up to expert mixed-strategy framing.
- **A spot classifier.** Every decision is tagged as value_raise / value_bet / value_call / bluff_catch / semi_bluff / pure_bluff / marginal / give_up — visible as a colored chip on the coach panel.
- **Bluffing math, not handwaving.** When the spot is a bluff candidate, the coach explains why: fold equity, MDF, α, the specific archetype's calling tendency. When it's a bluff-catcher, the coach says how often villain needs to bluff for the call to be +EV vs this specific opponent.
- **Skill rating** via Glicko-2, per category (preflop / flop / turn / river / overall). Each decision is scored as `delta_ev = EV(your action) − EV(EV-max action)`, normalized by big blind, mapped to a Glicko micro-match.
- **Leak detection.** The most common blunder type in the last 100 decisions feeds the recommender and (at high ratings) gets exploited by GTO regs at the table.

## Stack and decisions

| Concern | Choice | Why |
|---|---|---|
| Hand evaluation | Treys | Fastest pure-Python evaluator; lower index = stronger hand makes EV comparisons natural |
| Equity | Monte Carlo via NumPy partial-Fisher–Yates sampling | 10k samples per decision under 80ms; cached by (hero, board, range_hash) |
| Server | FastAPI + WebSocket | Live hand loop needs push to client; FastAPI's WebSocket API is the cleanest |
| Frontend | Vanilla JS + inline SVG, no build step | Astro at the wrapper layer (Magech), but the trainer itself stays buildless so the GitHub repo is the deploy artifact |
| Storage | SQLite via aiosqlite | One file, zero config, async-friendly, plenty for single-user trainer |
| LLM coach polish | Anthropic SDK (Opus for explanations, Haiku for opponent table-talk) | Deterministic math first; LLM polishes prose only |
| Cards UI | Inline SVG with felt-gradient defs | Card rendering ships with the page; no PNG sprites to load |
| Hosting | Cloudflare Tunnel → R620 → Docker | Reuses OpenClaw's homelab; free tier handles the load |

## What it can't do yet

A few honest limitations worth flagging:

- **Solver-grounded benchmarks.** The "ideal action" is EV-max against estimated ranges, which is correct against exploitable archetypes but isn't true GTO. A real solver (PioSOLVER / GTO+) integration is on the roadmap; the coach phrasing avoids claiming GTO for now.
- **Tournament mode.** Cash game only. ICM, push-fold ranges, bubble dynamics are conceptually scoped but not built.
- **Multi-table.** Single table per session. A 6-max + a 9-max running in parallel would require careful state isolation that hasn't been designed yet.
- **Mobile.** The SVG table is desktop-shaped. The drill modal works on mobile, the table doesn't.

## Why it's here

The pedagogy idea — pilot-style axis-by-axis unlock with adaptive-frequency quizzes — works for anything dense and multi-skill: chess openings, music theory, even debugging stacktraces. The Felt is the first build of it; the architecture is portable.

[Try the demo →](https://felt.magech.ai) · [Source on GitHub →](https://github.com/azrael92/the-felt)
