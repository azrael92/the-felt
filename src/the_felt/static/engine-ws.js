/**
 * engine-ws.js
 *
 * Intercepts window.WebSocket and runs the full 6-max NLHE poker engine
 * client-side. Designed for Cloudflare Pages deployments with no backend.
 *
 * Load order: this file must be loaded BEFORE app.js.
 *
 * Sections:
 *   1. Fetch monkey-patch (API stubs)
 *   2. Card utilities
 *   3. Hand evaluator
 *   4. Monte Carlo equity
 *   5. Outs estimation
 *   6. Coach math
 *   7. Archetype bot AI
 *   8. Glicko-2 simplified tracker
 *   9. FeltEngine game engine
 *  10. MockWebSocket
 *  11. Install override
 */

'use strict';

// ─────────────────────────────────────────────────────────────────────────────
// 0. IFRAME DETECTION — force two-column layout when embedded
// ─────────────────────────────────────────────────────────────────────────────

(function detectEmbed() {
  try {
    if (window.self !== window.top) {
      document.documentElement.classList.add('in-iframe');
    }
  } catch (e) {
    // cross-origin parent: must be in an iframe
    document.documentElement.classList.add('in-iframe');
  }
})();

// ─────────────────────────────────────────────────────────────────────────────
// 1. FETCH MONKEY-PATCH
// ─────────────────────────────────────────────────────────────────────────────

(function patchFetch() {
  const _origFetch = window.fetch;
  function jsonResponse(obj) {
    return Promise.resolve(new Response(JSON.stringify(obj), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
  }
  window.fetch = async function(url, opts) {
    if (typeof url === 'string') {
      if (url.includes('/api/lessons'))       return jsonResponse({ modules: LESSON_CATALOG });
      if (url.includes('/api/users/'))        return jsonResponse({ progress: {} });
      if (url.includes('/api/stages'))        return jsonResponse({ stages: [] });
      if (url.includes('/api/config'))        return jsonResponse({});
    }
    return _origFetch(url, opts);
  };
})();

// ─────────────────────────────────────────────────────────────────────────────
// 2. CARD UTILITIES
// ─────────────────────────────────────────────────────────────────────────────

const RANKS = ['2','3','4','5','6','7','8','9','T','J','Q','K','A'];
const SUITS = ['s','h','d','c'];
const RANK_VAL = {};
RANKS.forEach((r, i) => { RANK_VAL[r] = i + 2; }); // 2=2 … A=14

function rankOf(card) { return RANK_VAL[card[0]]; }
function suitOf(card) { return card[1]; }

/** Build a full 52-card deck. */
function buildDeck() {
  const deck = [];
  for (const r of RANKS) for (const s of SUITS) deck.push(r + s);
  return deck;
}

/**
 * Shuffle `deck` in-place using Fisher-Yates from position `start` onward.
 * Returns the deck reference.
 */
function shuffleFrom(deck, start = 0) {
  for (let i = deck.length - 1; i > start; i--) {
    const j = start + Math.floor(Math.random() * (i - start + 1));
    [deck[i], deck[j]] = [deck[j], deck[i]];
  }
  return deck;
}

/** Deal `n` cards from front of `remaining` array (removes them). */
function dealN(remaining, n) {
  return remaining.splice(0, n);
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. HAND EVALUATOR
// ─────────────────────────────────────────────────────────────────────────────
// Categories: 8=StraightFlush, 7=Quads, 6=FullHouse, 5=Flush,
//             4=Straight,      3=Trips, 2=TwoPair,   1=Pair,  0=HighCard
// Score = [category, ...tiebreakers]  — compare lexicographically.

/**
 * Evaluate exactly 5 cards. Returns [category, ...tiebreakers].
 */
function evalHand5(cards) {
  const rv = cards.map(rankOf).sort((a, b) => b - a); // desc
  const sv = cards.map(suitOf);
  const isFlush = sv.every(s => s === sv[0]);

  // Check straight (including A-low wheel: A-2-3-4-5)
  let isStraight = false;
  let straightHigh = rv[0];
  if (rv[0] - rv[4] === 4 && new Set(rv).size === 5) {
    isStraight = true;
  } else if (rv[0] === 14 && rv[1] === 5 && rv[2] === 4 && rv[3] === 3 && rv[4] === 2) {
    isStraight = true;
    straightHigh = 5; // wheel
  }

  if (isFlush && isStraight) return [8, straightHigh];

  // Count rank frequencies
  const freq = {};
  rv.forEach(r => { freq[r] = (freq[r] || 0) + 1; });
  const counts = Object.entries(freq)
    .map(([r, c]) => [parseInt(r), c])
    .sort((a, b) => b[1] - a[1] || b[0] - a[0]); // sort by count desc, rank desc

  const groups = counts.map(([, c]) => c);

  if (groups[0] === 4) {
    // Quads
    return [7, counts[0][0], counts[1][0]];
  }
  if (groups[0] === 3 && groups[1] === 2) {
    // Full house
    return [6, counts[0][0], counts[1][0]];
  }
  if (isFlush) {
    return [5, ...rv];
  }
  if (isStraight) {
    return [4, straightHigh];
  }
  if (groups[0] === 3) {
    // Trips
    return [3, counts[0][0], counts[1][0], counts[2][0]];
  }
  if (groups[0] === 2 && groups[1] === 2) {
    // Two pair
    const pairs = counts.filter(([, c]) => c === 2).map(([r]) => r).sort((a,b)=>b-a);
    const kicker = counts.find(([, c]) => c === 1)[0];
    return [2, pairs[0], pairs[1], kicker];
  }
  if (groups[0] === 2) {
    // One pair
    const pair = counts[0][0];
    const kickers = counts.slice(1).map(([r]) => r).sort((a,b)=>b-a);
    return [1, pair, ...kickers];
  }
  // High card
  return [0, ...rv];
}

/** All C(n,k) combinations of indices. */
function combinations(n, k) {
  const result = [];
  const combo = [];
  function helper(start) {
    if (combo.length === k) { result.push([...combo]); return; }
    for (let i = start; i < n; i++) {
      combo.push(i);
      helper(i + 1);
      combo.pop();
    }
  }
  helper(0);
  return result;
}

/** Best 5-card hand from up to 7 cards. */
function bestHand7(hole2, boardN) {
  const all = [...hole2, ...boardN];
  const n = all.length;
  if (n < 5) return evalHand5(all.concat(Array(5 - n).fill('2s'))); // fallback (shouldn't happen)
  const combos = combinations(n, 5);
  let best = null;
  for (const idx of combos) {
    const hand = idx.map(i => all[i]);
    const score = evalHand5(hand);
    if (!best || compareScores(score, best) > 0) best = score;
  }
  return best;
}

/** Compare two score arrays. Returns 1, 0, or -1. */
function compareScores(a, b) {
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const av = a[i] ?? 0, bv = b[i] ?? 0;
    if (av > bv) return 1;
    if (av < bv) return -1;
  }
  return 0;
}

const CAT_NAMES = [
  'High Card','Pair','Two Pair','Three of a Kind','Straight',
  'Flush','Full House','Four of a Kind','Straight Flush',
];
const RANK_NAMES = {
  2:'Twos',3:'Threes',4:'Fours',5:'Fives',6:'Sixes',7:'Sevens',
  8:'Eights',9:'Nines',10:'Tens',11:'Jacks',12:'Queens',13:'Kings',14:'Aces',
};
const RANK_NAME_S = {
  2:'Two',3:'Three',4:'Four',5:'Five',6:'Six',7:'Seven',
  8:'Eight',9:'Nine',10:'Ten',11:'Jack',12:'Queen',13:'King',14:'Ace',
};

/** Human-readable description of a score array. */
function describeScore(score) {
  const cat = score[0];
  const n = (r) => RANK_NAMES[r] || r;
  const ns = (r) => RANK_NAME_S[r] || r;
  switch (cat) {
    case 8: return score[1] === 14 ? 'Royal Flush' : `Straight Flush, ${ns(score[1])} high`;
    case 7: return `Four of a Kind, ${n(score[1])}`;
    case 6: return `Full House, ${n(score[1])} over ${n(score[2])}`;
    case 5: return `Flush, ${ns(score[1])} high`;
    case 4: return `Straight, ${ns(score[1])} high`;
    case 3: return `Three of a Kind, ${n(score[1])}`;
    case 2: return `Two Pair, ${n(score[1])} and ${n(score[2])}`;
    case 1: return `Pair of ${n(score[1])}`;
    case 0: return `High Card, ${ns(score[1])}`;
    default: return 'Unknown';
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 4. MONTE CARLO EQUITY
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Estimate hero equity by random simulation.
 * @param {string[]} holeCards  - hero's 2 cards
 * @param {string[]} board      - 0-5 community cards already dealt
 * @param {number}   numOpponents
 * @param {number}   iters
 * @returns {number} equity 0-1
 */
function calcEquity(holeCards, board, numOpponents, iters = 400) {
  const known = new Set([...holeCards, ...board]);
  const remaining = buildDeck().filter(c => !known.has(c));
  const needed = Math.max(0, 5 - board.length); // 0 on river

  let wins = 0, ties = 0;

  for (let i = 0; i < iters; i++) {
    // Partial Fisher-Yates — deal community runout + opponent hole cards
    const deck = [...remaining];
    const totalNeeded = needed + numOpponents * 2;
    for (let j = 0; j < totalNeeded; j++) {
      const r = j + Math.floor(Math.random() * (deck.length - j));
      [deck[j], deck[r]] = [deck[r], deck[j]];
    }
    // On river (needed=0) simBoard == board
    const simBoard = needed > 0 ? [...board, ...deck.slice(0, needed)] : board;
    const heroScore = bestHand7(holeCards, simBoard);
    let heroWins = true, heroTie = false;
    for (let p = 0; p < numOpponents; p++) {
      const oppHole = [deck[needed + p * 2], deck[needed + p * 2 + 1]];
      const oppScore = bestHand7(oppHole, simBoard);
      const cmp = compareScores(heroScore, oppScore);
      if (cmp < 0) { heroWins = false; heroTie = false; break; }
      if (cmp === 0) heroTie = true;
    }
    if (heroWins && !heroTie) wins++;
    else if (heroTie) ties += 0.5;
  }
  return (wins + ties) / iters;
}

// ─────────────────────────────────────────────────────────────────────────────
// 5. OUTS ESTIMATION
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Estimate drawing outs for hero on current board.
 * @param {string[]} holeCards
 * @param {string[]} board
 * @returns {number} outs
 */
function estimateOuts(holeCards, board) {
  if (board.length === 0) return 0;
  const all = [...holeCards, ...board];
  let outs = 0;

  // Flush draw: 4 cards of same suit
  const suitCount = {};
  all.forEach(c => { const s = suitOf(c); suitCount[s] = (suitCount[s] || 0) + 1; });
  const flushDraw = Object.values(suitCount).some(v => v === 4);
  if (flushDraw) outs += 9;

  // Straight draw: look at sorted rank set
  const ranks = [...new Set(all.map(rankOf))].sort((a, b) => a - b);
  // Open-ended straight draw: 4 consecutive ranks
  let oesd = false, gutshot = false;
  for (let i = 0; i <= ranks.length - 4; i++) {
    const window = ranks.slice(i, i + 4);
    if (window[3] - window[0] === 3) {
      // 4 consecutive
      if (window[0] > 2 && window[3] < 14) oesd = true;
      else gutshot = true;
    } else if (window[3] - window[0] === 4 && new Set(window).size === 4) {
      // 4 cards spanning 5 slots = gutshot
      const missing = [];
      for (let v = window[0]; v <= window[3]; v++) {
        if (!window.includes(v)) missing.push(v);
      }
      if (missing.length === 1) gutshot = true;
    }
  }
  // Straight outs: when also holding a flush draw, ~2 straight cards share the flush suit
  const straightOverlap = flushDraw ? 2 : 0;
  if (oesd) outs += Math.max(0, 8 - straightOverlap);
  else if (gutshot) outs += Math.max(0, 4 - straightOverlap);

  // Overcards to board (2 overcards = ~6 outs)
  if (board.length >= 3) {
    const boardRanks = board.map(rankOf);
    const maxBoard = Math.max(...boardRanks);
    const overcards = holeCards.filter(c => rankOf(c) > maxBoard);
    if (overcards.length === 2 && outs === 0) outs += 6;
    else if (overcards.length === 1 && outs === 0) outs += 3;
  }

  return outs;
}

// ─────────────────────────────────────────────────────────────────────────────
// 6. COACH MATH
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Compute full coach tip payload.
 *
 * EV model: pure pot-equity math, no fake fold-frequency credits.
 * Verdict thresholds:
 *   Fold   — equity < alpha (not getting pot odds)
 *   Call   — alpha <= equity < alpha + 0.18 (getting odds but no strong edge)
 *   Raise  — equity >= alpha + 0.18 (clear equity advantage worth building the pot)
 *   Bet    — no call amount and equity >= 0.54 (slight favourite, bet for value)
 *   Check  — no call amount and equity < 0.54 (marginal, don't inflate pot)
 */
function computeCoach(holeCards, board, pot, callAmount, heroStack, numOpponents, userAction, handId, seq) {
  const equity = calcEquity(holeCards, board, numOpponents, 400);

  // alpha = equity needed to break even on a call
  const alpha = callAmount > 0 ? callAmount / (pot + callAmount) : 0;
  const mdf   = 1 - alpha;
  const edge  = equity - alpha;
  const outs  = estimateOuts(holeCards, board);

  // Outs-based river chance estimates
  const next_card_pct = board.length < 5 ? outs / Math.max(1, 48 - board.length) : 0;
  const by_river_pct  = board.length === 3 ? 1 - Math.pow(1 - outs / 47, 2)
                      : board.length === 4 ? outs / 46
                      : 0;

  // EV of each action — no fake fold equity, just equity × pot math
  const ev_fold = 0;

  // Call EV: win (pot + callAmount) at equity%, lose callAmount at (1-equity)%
  const ev_call = callAmount > 0
    ? +(equity * (pot + callAmount) - (1 - equity) * callAmount).toFixed(2)
    : +(equity * pot).toFixed(2);

  // Bet/raise EV: use 70% pot sizing, no fold-equity assumption
  // This is conservative and avoids the "always go all-in" trap
  const betAmt   = Math.min(heroStack, Math.round(pot * 0.70));
  const raiseAmt = Math.min(heroStack, Math.max(callAmount * 2.5, Math.round(pot * 0.70)));
  const ev_bet   = +(equity * (pot + betAmt)   - (1 - equity) * betAmt).toFixed(2);
  const ev_raise = callAmount > 0
    ? +(equity * (pot + raiseAmt) - (1 - equity) * raiseAmt).toFixed(2)
    : ev_bet;

  // ── Verdict ────────────────────────────────────────────────────────────────
  let verdict;
  if (callAmount === 0) {
    // Betting or checking spot
    verdict = equity >= 0.54 ? 'bet' : 'check';
  } else {
    if (equity >= alpha + 0.18) {
      // Clear equity advantage — raise is best
      verdict = 'raise';
    } else if (equity >= alpha) {
      // Getting pot odds — call
      verdict = 'call';
    } else {
      // Not getting the price — fold
      verdict = 'fold';
    }
  }

  const verdictMap = { fold: 'Fold', check: 'Check', call: 'Call', bet: 'Bet', raise: 'Raise' };

  // Coaching notes
  const notes = [];
  if (outs > 0) notes.push(`${outs} outs to improve`);
  if (equity >= 0.65)       notes.push('Strong equity — lean toward building the pot');
  else if (equity <= 0.28)  notes.push('Well behind — only continue with a clear reason');
  if (callAmount > 0) {
    if (edge > 0.12)        notes.push(`${(edge * 100).toFixed(0)}pp above pot odds — comfortable call or raise`);
    else if (edge < -0.08)  notes.push(`${(Math.abs(edge) * 100).toFixed(0)}pp below pot odds — folding saves chips`);
    else                    notes.push('Roughly at pot odds — marginal call, close decision');
  }
  if (board.length >= 3 && outs >= 9) notes.push('Flush draw: ~35% to hit by river');
  if (board.length === 3 && outs >= 8 && outs < 9) notes.push('Open-ended straight draw: ~32% to hit by river');

  const spot = board.length === 0 ? 'preflop' :
               board.length === 3 ? 'flop'    :
               board.length === 4 ? 'turn'    : 'river';

  return {
    hand_id: handId,
    seq,
    tier: 1,
    to_call: callAmount,
    spot,
    math: {
      equity:            +equity.toFixed(3),
      pot_odds_required: +alpha.toFixed(3),
      edge:              +edge.toFixed(3),
      mdf:               +mdf.toFixed(3),
      alpha:             +alpha.toFixed(3),
      outs,
      next_card_pct:     +next_card_pct.toFixed(3),
      by_river_pct:      +by_river_pct.toFixed(3),
      ev_by_action: {
        fold:  0,
        call:  callAmount > 0 ? ev_call : ev_fold,
        check: callAmount === 0 ? ev_call : ev_fold,
        bet:   ev_bet,
        raise: ev_raise,
      },
      ev_labels: {
        fold:  'Fold',
        call:  callAmount > 0 ? 'Call' : 'Check',
        check: 'Check',
        bet:   'Bet',
        raise: 'Raise',
      },
      verdict,
      verdict_label:  verdictMap[verdict] || verdict,
      verdict_button: verdict,
      notes,
    },
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// 7. ARCHETYPE BOT AI
// ─────────────────────────────────────────────────────────────────────────────

const ARCHETYPES = {
  nit: { vpip: 0.12, cbet: 0.35, aggression: 0.25 },
  tag: { vpip: 0.18, cbet: 0.70, aggression: 0.65 },
  lag: { vpip: 0.40, cbet: 0.80, aggression: 0.85 },
  lp:  { vpip: 0.45, cbet: 0.20, aggression: 0.15 },
};

/**
 * Decide bot action.
 * @param {string} archetype
 * @param {number} handStrength  0-1 (normalized)
 * @param {number} pot
 * @param {number} callAmount
 * @param {number} minRaise
 * @param {number} stack
 * @param {string} street        'preflop'|'flop'|'turn'|'river'
 * @param {boolean} isPfAggressor  was this bot the preflop aggressor?
 * @returns {{ action: string, amount: number }}
 */
function botDecide(archetype, handStrength, pot, callAmount, minRaise, stack, street, isPfAggressor) {
  const arch = ARCHETYPES[archetype] || ARCHETYPES.tag;
  const { vpip, cbet, aggression } = arch;
  const r = Math.random();

  // Preflop: decide whether to play at all
  if (street === 'preflop') {
    const willPlay = handStrength > (1 - vpip);
    if (!willPlay && callAmount > 0) return { action: 'fold', amount: 0 };
    if (!willPlay && callAmount === 0) return { action: 'check', amount: 0 };
    // Strong hand: raise sometimes
    if (handStrength > 0.80 || r < aggression * 0.6) {
      const raiseSize = Math.min(stack, Math.max(minRaise, pot * (1.5 + r)));
      if (raiseSize > callAmount && stack > callAmount) return { action: 'raise', amount: Math.floor(raiseSize) };
    }
    if (callAmount > 0) return { action: 'call', amount: callAmount };
    return { action: 'check', amount: 0 };
  }

  // Post-flop
  const isCbetSpot = isPfAggressor && street === 'flop';

  if (handStrength > 0.80) {
    // Value bet / raise
    if (callAmount > 0 && r < aggression) {
      const raiseSize = Math.min(stack, Math.max(minRaise, pot * (0.5 + r * 0.5)));
      if (raiseSize > callAmount) return { action: 'raise', amount: Math.floor(raiseSize) };
    }
    if (callAmount > 0) return { action: 'call', amount: callAmount };
    // Bet for value
    const betSize = Math.min(stack, Math.floor(pot * (0.5 + r * 0.5)));
    if (betSize > 0) return { action: 'bet', amount: betSize };
    return { action: 'check', amount: 0 };
  }

  if (handStrength >= 0.45) {
    // Medium hand
    if (callAmount > 0) {
      if (r < aggression * 0.3) {
        const raiseSize = Math.min(stack, Math.max(minRaise, pot * (0.6 + r * 0.4)));
        if (raiseSize > callAmount) return { action: 'raise', amount: Math.floor(raiseSize) };
      }
      // Call if pot odds are reasonable
      if (callAmount <= pot * 0.6) return { action: 'call', amount: callAmount };
      return { action: 'fold', amount: 0 };
    }
    // Check or bet
    const willBet = isCbetSpot ? r < cbet : r < aggression * 0.5;
    if (willBet) {
      const betSize = Math.min(stack, Math.floor(pot * (0.4 + r * 0.4)));
      if (betSize > 0) return { action: 'bet', amount: betSize };
    }
    return { action: 'check', amount: 0 };
  }

  // Weak hand
  if (callAmount > 0) {
    if (r < 0.1) return { action: 'call', amount: callAmount }; // bluff-catch occasionally
    return { action: 'fold', amount: 0 };
  }
  // Check or bluff
  if (r < aggression * 0.25) {
    const betSize = Math.min(stack, Math.floor(pot * (0.5 + r)));
    if (betSize > 0) return { action: 'bet', amount: betSize };
  }
  return { action: 'check', amount: 0 };
}

/** Assign normalized hand strength 0-1 based on hole cards + board context. */
function estimateHandStrength(holeCards, board, numOpponents) {
  // Quick proxy: pre-computed equity (fewer iters for speed)
  return calcEquity(holeCards, board, numOpponents, 60);
}

// ─────────────────────────────────────────────────────────────────────────────
// 8. LESSON CATALOG + DRILL ENGINE
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Static lesson catalog.  Each lesson has a pool of question variants; the
 * drill engine picks one at random on every `start_drill` call.
 *
 * Format consumed by app.js:
 *   catalog  → { modules: [ { id, title, lessons: [ { id, title, drill_kind } ] } ] }
 *   question → { question, context?, answer_type: 'mc'|'numeric', choices?, correct_index? }
 *   feedback → { correct, correct_answer, explanation }
 */
const LESSON_CATALOG = [
  {
    id: 'pot-odds',
    title: 'Pot Odds',
    lessons: [
      { id: 'po-read',    title: 'Reading pot odds',          drill_kind: 'mc' },
      { id: 'po-call',    title: 'Should I call?',            drill_kind: 'mc' },
      { id: 'po-rule24',  title: 'Rule of 2 and 4',          drill_kind: 'mc' },
      { id: 'po-flush',   title: 'Flush draw math',           drill_kind: 'mc' },
    ],
  },
  {
    id: 'hand-strength',
    title: 'Hand Strength',
    lessons: [
      { id: 'hs-rank',    title: 'Ranking made hands',        drill_kind: 'mc' },
      { id: 'hs-texture', title: 'Wet vs dry boards',         drill_kind: 'mc' },
      { id: 'hs-tptk',   title: 'Top pair vs overpair',      drill_kind: 'mc' },
      { id: 'hs-draws',   title: 'Drawing hands vs made',     drill_kind: 'mc' },
    ],
  },
  {
    id: 'position',
    title: 'Position',
    lessons: [
      { id: 'pos-basics', title: 'Why position matters',      drill_kind: 'mc' },
      { id: 'pos-steal',  title: 'Late-position opens',       drill_kind: 'mc' },
      { id: 'pos-blind',  title: 'Playing from the blinds',   drill_kind: 'mc' },
    ],
  },
  {
    id: 'bet-sizing',
    title: 'Bet Sizing',
    lessons: [
      { id: 'bs-value',   title: 'Value bet sizing',          drill_kind: 'mc' },
      { id: 'bs-cbet',    title: 'Continuation betting',      drill_kind: 'mc' },
      { id: 'bs-bluff',   title: 'Bluff sizing',              drill_kind: 'mc' },
    ],
  },
];

/**
 * Per-lesson question pools.  Each entry is one drill variant:
 *   { q, choices, correct, explanation }
 * `correct` is the 0-based index of the right choice.
 */
const DRILL_QUESTIONS = {
  'po-read': [
    {
      q: 'The pot is 60 chips. Your opponent bets 20 chips. What pot odds are you being offered?',
      choices: ['25%', '20%', '33%', '17%'],
      correct: 0,
      explanation: 'You must call 20 into a pot of 80 (60 + 20). 20 ÷ 80 = 25%. You need at least 25% equity to profit.',
    },
    {
      q: 'The pot is 100 chips. Your opponent bets 50 chips. What equity do you need to break even on a call?',
      choices: ['33%', '25%', '50%', '40%'],
      correct: 0,
      explanation: 'Call 50 into pot of 150 (100+50). 50 ÷ 150 = 33%. That\'s your pot odds — the minimum equity needed.',
    },
    {
      q: 'The pot is 40. Opponent bets 40 (pot-sized). What are your pot odds?',
      choices: ['50%', '33%', '40%', '25%'],
      correct: 0,
      explanation: 'Call 40 into 120 (40+40+40). 40 ÷ 120 = 33%… wait — it\'s 40 into 80+40=120: 40/120 = 33%. Actually the call is 40 into the new pot of 40+40+40=120, so 33%. A pot-sized bet always gives the caller 33% pot odds.',
    },
    {
      q: 'Pot is 200. Opponent bets 100 (half-pot). You must call 100. What are your pot odds?',
      choices: ['25%', '33%', '50%', '20%'],
      correct: 0,
      explanation: 'Call 100 into 400 (200+100+100). 100 ÷ 400 = 25%. A half-pot bet gives the caller 25% pot odds.',
    },
  ],
  'po-call': [
    {
      q: 'You hold a flush draw (≈35% equity). Pot is 100, opponent bets 50. Should you call?',
      choices: ['Yes — you have more equity than pot odds require', 'No — you don\'t have enough equity', 'Only if in position', 'Depends on your stack size'],
      correct: 0,
      explanation: 'Pot odds require 50÷(150+50) = 25% equity. Your flush draw gives ~35%. You have 10 percentage points of edge — a clear call.',
    },
    {
      q: 'You have top pair (≈55% equity). Pot is 80, opponent bets 80 (pot-size). Your pot odds are 33%. Should you call?',
      choices: ['Yes — 55% > 33%, you have strong edge', 'No — pot-sized bets mean strength', 'Yes but only to check the turn', 'Fold — top pair is not strong enough'],
      correct: 0,
      explanation: '55% equity vs 33% required = 22pp of edge. Call. The opponent\'s bet size doesn\'t change the math; only your equity and the price matter.',
    },
    {
      q: 'You have a gutshot straight draw (≈17% equity). Pot is 120, opponent bets 60. Pot odds: 33%. Should you call?',
      choices: ['No — 17% < 33%, you don\'t have the price', 'Yes — any draw is worth calling', 'Yes — implied odds make up the gap', 'Check to the turn instead'],
      correct: 0,
      explanation: '17% equity doesn\'t cover 33% pot odds. You lose money calling. Implied odds might help, but 16 percentage points is a big gap to overcome with future streets.',
    },
  ],
  'po-rule24': [
    {
      q: 'You\'re on the flop with 9 flush outs (two cards to come). Using the Rule of 4, what\'s your rough equity?',
      choices: ['36%', '18%', '27%', '45%'],
      correct: 0,
      explanation: 'Rule of 4: outs × 4 gives a quick equity estimate with two cards to come. 9 × 4 = 36%. (Actual is ~35%, so the rule is accurate.)',
    },
    {
      q: 'You\'re on the TURN with 8 straight outs (one card to come). Using the Rule of 2, what\'s your rough equity?',
      choices: ['16%', '32%', '8%', '24%'],
      correct: 0,
      explanation: 'Rule of 2: outs × 2 on the turn. 8 × 2 = 16%. (Actual is ~17%.) With one card left, use ×2, not ×4.',
    },
    {
      q: 'Why is the Rule of 4 only for the FLOP, not the turn?',
      choices: ['On the flop you have two cards to come; on the turn only one', 'The deck has fewer cards on the turn', 'Opponents show their hands on the turn', 'Pot sizes differ on each street'],
      correct: 0,
      explanation: 'Each out has roughly a 1-in-47 chance per card. Two cards ≈ 2×, so outs × 4 ≈ outs × 2 × 2. On the turn there\'s only one card left, so multiply by 2, not 4.',
    },
  ],
  'po-flush': [
    {
      q: 'You hold 9♥ 7♥ on a board of A♥ K♠ 3♥ (flop). How many flush outs do you have?',
      choices: ['9', '13', '7', '11'],
      correct: 0,
      explanation: 'There are 13 hearts total. You hold 2, the board has 2 — that\'s 4 hearts accounted for. 13 − 4 = 9 remaining flush outs.',
    },
    {
      q: 'Pot is 100, opponent bets 50. You have a flush draw (9 outs, ~36% equity on flop). Pot odds are 25%. Is calling correct?',
      choices: ['Yes — 36% > 25%', 'No — flush draws miss more than they hit', 'Only if the opponent is bluffing', 'No — you need implied odds'],
      correct: 0,
      explanation: 'Pure pot odds: 36% equity vs 25% required. You have 11 percentage points of edge. Call immediately without needing implied odds.',
    },
  ],

  'hs-rank': [
    {
      q: 'Which hand wins: two pair (Aces and Kings) or a set of Jacks?',
      choices: ['Set of Jacks — three of a kind beats two pair', 'Two pair — Aces and Kings is a premium hand', 'They split the pot', 'Depends on the kicker'],
      correct: 0,
      explanation: 'The hand ranking order is: pair < two pair < three of a kind (set) < straight < flush. A set of Jacks beats two pair regardless of which two pair.',
    },
    {
      q: 'Which of these is the strongest hand on a board of Q♠ J♦ T♣ 9♥ 2♠?',
      choices: ['K-x making a King-high straight', 'Q-Q making top set', 'J-9 making two pair', 'A-K making top pair'],
      correct: 0,
      explanation: 'The board runs Q-J-T-9. Any King makes a K-high straight (K-Q-J-T-9), which beats any set or two pair.',
    },
  ],
  'hs-texture': [
    {
      q: 'Board: A♠ K♥ 7♦. Is this board "wet" or "dry"?',
      choices: ['Dry — rainbow, no flush or straight draws', 'Wet — many draws possible', 'Semi-wet — one draw', 'Neutral'],
      correct: 0,
      explanation: 'Dry = rainbow suits, no connected ranks. A-K-7 is three different suits with gaps — very few draws connect. Strong made hands are safer here.',
    },
    {
      q: 'Board: 8♣ 7♣ 6♦. Is this board "wet" or "dry"?',
      choices: ['Wet — flush draw, many straight draws', 'Dry — small cards only', 'Dry — no pair on board', 'Semi-wet'],
      correct: 0,
      explanation: 'Two clubs = flush draw. 8-7-6 = OESD for any 5 or 9, gutshots for many others. Many hands connect here. Wet boards favour drawing hands and require larger bets for protection.',
    },
    {
      q: 'Why does board texture affect how much you should bet?',
      choices: ['Wet boards need larger bets to price out drawing hands', 'Dry boards need larger bets because opponents are stronger', 'Texture doesn\'t affect bet sizing', 'You always bet pot regardless of texture'],
      correct: 0,
      explanation: 'On wet boards, your opponent has many outs. A small bet gives them a cheap price. Bet larger to charge draws. On dry boards, a smaller bet still forces a tough decision.',
    },
  ],
  'hs-tptk': [
    {
      q: 'You hold A♠ K♣. Board: K♥ 7♦ 2♣. You have top pair top kicker. What hand beats you right now?',
      choices: ['A set (K-K, 7-7, or 2-2)', 'Any pair of Aces', 'Any King', 'Two overcards'],
      correct: 0,
      explanation: 'TPTK (K with A-kicker) loses to K-K (top set), 7-7 (middle set), or 2-2 (bottom set), and also two pair or better. On a dry board like this your TPTK is strong, but be wary when the board pairs.',
    },
    {
      q: 'You hold K♦ Q♠. Board: A♦ K♥ Q♣. You have two pair. What hand beats you?',
      choices: ['A set of Aces (A-A)', 'Any Ace', 'Any pair', 'Top pair top kicker'],
      correct: 0,
      explanation: 'A-A makes a set (three Aces), which beats your two pair. Also K-K or Q-Q would make a set. Two pair is strong but vulnerable to sets when the board is coordinated.',
    },
  ],
  'hs-draws': [
    {
      q: 'You have a flush draw on the flop. Your opponent bets. Why might you RAISE instead of just calling?',
      choices: ['To win the pot immediately if they fold, plus you still have equity if called', 'Because draws should always be played aggressively', 'Because raising disguises your hand', 'You should never raise a draw'],
      correct: 0,
      explanation: 'A semi-bluff raise has two ways to win: opponent folds (you win now) or they call and you hit your draw. This makes raises with draws profitable even when called.',
    },
    {
      q: 'You have 15 outs (flush draw + open-ended straight draw). Pot is 80, opponent bets 20. Pot odds ≈ 20%. Should you call?',
      choices: ['Yes — 15 outs is ~54% equity on flop, far above 20% required', 'No — draws are risky', 'Only call one more street', 'Fold, waiting for a made hand'],
      correct: 0,
      explanation: '15 outs × 4 (Rule of 4 on flop) ≈ 60% equity. That demolishes the 20% pot odds. This is actually a re-raise spot — you\'re a favourite to make your hand.',
    },
  ],

  'pos-basics': [
    {
      q: 'Why is the Button (BTN) considered the best position at the poker table?',
      choices: ['It acts last on every post-flop street, giving full information before deciding', 'It gets to see two extra cards', 'It pays no blinds', 'It has the largest stack'],
      correct: 0,
      explanation: 'Acting last means you see every opponent\'s action before making yours. You gain information on every betting round — check, bet, or raise with full context.',
    },
    {
      q: 'You\'re in the Big Blind and everyone folds to the Button, who raises. The Button is in position. What does that mean for the rest of the hand?',
      choices: ['They act after you on every post-flop street — they have an information advantage', 'You have the advantage because you get to raise first pre-flop', 'Position doesn\'t matter in heads-up pots', 'The Big Blind always has the positional advantage'],
      correct: 0,
      explanation: 'Out of position (OOP), you must act before seeing what the in-position player does. They can check behind (pot control) or bet when you check (exploiting your passivity). Position is a structural advantage that persists for the whole hand.',
    },
  ],
  'pos-steal': [
    {
      q: 'You\'re on the Button with 7♦ 4♠. Both blinds have tight, defensive stats. What is the correct play?',
      choices: ['Raise to steal the blinds — position + fold equity makes this profitable', 'Fold — 7-4 offsuit is too weak to play', 'Call and see the flop cheap', 'Limp in and hope for a good flop'],
      correct: 0,
      explanation: 'Against tight blinds, a button raise wins the pot outright often enough to be profitable even with junk. Plus you play in position if called. This is called a "steal."',
    },
    {
      q: 'You open-raise from the Cutoff (one before the Button). The Button calls. You\'re now OUT of position for the rest of the hand. Why?',
      choices: ['The Button acts after you on every post-flop street', 'You have a weaker hand range', 'The Cutoff always acts first', 'You should have limped instead'],
      correct: 0,
      explanation: 'On the flop, turn, and river the Button acts last among the remaining players. From the Cutoff you\'re OOP against the BTN.',
    },
  ],
  'pos-blind': [
    {
      q: 'You\'re in the Big Blind and face a Button open-raise. You have a decent hand. Why is calling harder than if you were on the Button?',
      choices: ['You\'ll be OOP for all post-flop streets — you act first every time', 'The Button always has a better hand', 'You can\'t re-raise from the blinds', 'The blinds are forced bets'],
      correct: 0,
      explanation: 'Defending the BB means playing out of position for the entire hand. You need more equity or a very strong hand to compensate for the informational disadvantage.',
    },
  ],

  'bs-value': [
    {
      q: 'You have top set on the river (a near-nut hand). Pot is 200. What is generally the best approach?',
      choices: ['Bet large — extract maximum value from worse hands that might call', 'Bet small — don\'t scare them away', 'Check — let them bluff into you', 'Go all-in every time'],
      correct: 0,
      explanation: 'With a near-nut hand and a capped opponent range, a large bet extracts more value. Opponents with strong-but-worse hands (two pair, lower sets) are likely to call. Small bets leave money on the table.',
    },
    {
      q: 'You have a medium-strength hand (top pair, weak kicker). The pot is 100 and you want to bet for thin value but also get information. What sizing makes sense?',
      choices: ['33–50% pot — invites calls from worse, keeps the pot controlled', '100% pot — maximize value', '10% pot — look weak', 'Check and call down'],
      correct: 0,
      explanation: 'Thin value bets use smaller sizing: you want weaker hands to call, but you don\'t want to bloat the pot with a marginal holding. 33–50% pot is typical.',
    },
  ],
  'bs-cbet': [
    {
      q: 'You raised pre-flop with A♠ K♦ and got one caller. The flop comes J♥ 8♣ 3♦ — a dry, low board that missed you. Should you continuation bet?',
      choices: ['Yes — you have pre-flop initiative and this board favours your range', 'No — you missed the flop completely', 'Only if your opponent checks first', 'Check and give up'],
      correct: 0,
      explanation: 'As the pre-flop raiser you represent a strong range (big pairs, AK, AQ). A dry low board doesn\'t help many calling hands. A small c-bet (30–40% pot) takes the pot frequently against one opponent.',
    },
    {
      q: 'When should you generally SKIP the continuation bet?',
      choices: ['On wet, connected boards against multiple opponents', 'Whenever you miss the flop', 'When you have position', 'Against tight players only'],
      correct: 0,
      explanation: 'Wet boards (flush draws, straight draws) and multiple opponents reduce the success rate of c-bets. Your "fold equity" drops, opponents call or raise more, and you risk building a big pot with a weak hand.',
    },
  ],
  'bs-bluff': [
    {
      q: 'You\'re bluffing on the river. Pot is 200. What sizing gives you the best risk-reward ratio?',
      choices: ['Full pot (200) — forces opponent to need 33% equity to call profitably', 'Half pot — keeps it cheap', 'Over-pot — maximum pressure', 'Any size works equally'],
      correct: 0,
      explanation: 'A pot-sized river bluff requires the opponent to be right more than 33% of the time to call. If their calling range is accurate less than 33%, your bluff has positive EV. Larger bets require them to fold more often but also risk more when called.',
    },
    {
      q: 'Which hand is better to bluff with on the river?',
      choices: ['A busted draw with no showdown value', 'A medium pair that beats some hands', 'Top pair top kicker', 'Two pair'],
      correct: 0,
      explanation: 'Bluff with hands that have no showdown value (they lose to any made hand). A busted flush draw can\'t win by checking — bluffing is its only path to winning. Medium pairs should be checked for thin value or a bluff-catch.',
    },
  ],
};

/** State for the in-progress drill (lives in MockWebSocket) */
let _activeDrill = null;

/**
 * Called by MockWebSocket when the app sends start_drill.
 * Returns a drill_question message for the given lesson.
 */
function pickDrillQuestion(lessonId) {
  const pool = DRILL_QUESTIONS[lessonId];
  if (!pool || !pool.length) return null;
  const variant = pool[Math.floor(Math.random() * pool.length)];
  _activeDrill = { lessonId, variant };
  return {
    question: variant.q,
    answer_type: 'mc',
    choices: variant.choices,
  };
}

/**
 * Called when app sends submit_drill_answer.
 * Returns drill_feedback.
 */
function gradeDrillAnswer(answerIndex) {
  if (!_activeDrill) return { correct: false, correct_answer: '—', explanation: 'No active drill.' };
  const { variant } = _activeDrill;
  const correct = answerIndex === variant.correct;
  return {
    correct,
    correct_answer: variant.choices[variant.correct],
    explanation: variant.explanation,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// 9. SIMPLIFIED GLICKO-2 TRACKER
// ─────────────────────────────────────────────────────────────────────────────

class RatingTracker {
  constructor() {
    this.mu = 1500;
    this.streak = { current: 0, best: 0 };
  }

  /** Update rating given the hero's action vs the optimal action EV. */
  update(evUserAction, evBestAction, BB) {
    const delta_ev_bb = (evUserAction - evBestAction) / BB;
    let bucket;
    if (delta_ev_bb >= 0)         bucket = evUserAction >= evBestAction - 0.001 ? 'great' : 'fine';
    else if (delta_ev_bb >= -0.5) bucket = 'fine';
    else if (delta_ev_bb >= -2.0) bucket = 'minor_leak';
    else                          bucket = 'blunder';

    const delta = { great: 15, fine: 5, minor_leak: -10, blunder: -30 }[bucket];
    this.mu = Math.max(100, Math.min(3000, this.mu + delta));

    if (bucket === 'great' || bucket === 'fine') {
      this.streak.current++;
      if (this.streak.current > this.streak.best) this.streak.best = this.streak.current;
    } else {
      this.streak.current = 0;
    }

    return { overall_mu: Math.round(this.mu), delta_ev_bb: +delta_ev_bb.toFixed(2), bucket, streak: { ...this.streak } };
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 9. FELT ENGINE
// ─────────────────────────────────────────────────────────────────────────────

const BB = 10;
const SB = 5;
const START_STACK = 1000;
const NUM_SEATS = 6;
const BOT_ARCHETYPES = ['nit', 'tag', 'lag', 'lp', 'tag', 'nit'];
const BOT_NAMES = ['Alice', 'Bruno', 'Charlie', 'Diana', 'Evan', 'Faye'];
const POSITIONS_6MAX = ['BTN', 'SB', 'BB', 'UTG', 'HJ', 'CO']; // relative to button seat 0

/** Delay helper for bot "thinking". */
function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

/** Uniform random integer [lo, hi]. */
function randInt(lo, hi) {
  return lo + Math.floor(Math.random() * (hi - lo + 1));
}

class FeltEngine {
  constructor(ws) {
    this._ws = ws;           // reference to MockWebSocket (for _receive)
    this._handId = 0;
    this._seq = 0;
    this._buttonSeat = 0;    // rotates each hand
    this._running = false;
    this._waitingForHero = false;
    this._heroAction = null; // resolve fn
    this._rating = new RatingTracker();

    // Persistent stacks (survive hands)
    this._stacks = Array(NUM_SEATS).fill(START_STACK);

    // Hero info (set on join)
    this._heroId = null;
    this._heroName = 'Hero';
  }

  /** Called when app.js sends {type:'join', data:{user_id, name}} */
  async onJoin(data) {
    this._heroId = data.user_id || 'hero-001';
    this._heroName = data.name || 'Hero';
    this._ws._receive('joined', { user_id: this._heroId, name: this._heroName });
    if (!this._running) {
      this._running = true;
      this._runLoop().catch(err => console.error('[FeltEngine] loop error:', err));
    }
  }

  /** Called when app.js sends {type:'action', data:{action, amount}} */
  onHeroAction(data) {
    if (this._heroActionResolve) {
      this._heroActionResolve(data);
      this._heroActionResolve = null;
    }
  }

  /** Called when app.js sends {type:'next_hand'} */
  onNextHand() {
    // The loop handles this naturally; just wake if paused
    if (this._nextHandResolve) {
      this._nextHandResolve();
      this._nextHandResolve = null;
    }
  }

  /** Wait for hero to send an action. */
  _waitHeroAction() {
    return new Promise(resolve => {
      this._heroActionResolve = resolve;
    });
  }

  /** Wait for next_hand signal. */
  _waitNextHand() {
    return new Promise(resolve => {
      this._nextHandResolve = resolve;
    });
  }

  // ── Main game loop ─────────────────────────────────────────────────────────

  async _runLoop() {
    while (this._running) {
      try {
        await this._runHand();
      } catch (err) {
        console.error('[FeltEngine] hand error:', err);
      }
      // Wait for next_hand trigger or a short auto-advance
      await Promise.race([
        this._waitNextHand(),
        delay(2000),
      ]);
    }
  }

  // ── Single hand ────────────────────────────────────────────────────────────

  async _runHand() {
    this._handId++;
    this._seq = 0;
    const handId = this._handId;

    // Rebuild stacks for any busted players
    for (let i = 0; i < NUM_SEATS; i++) {
      if (this._stacks[i] < BB) this._stacks[i] = START_STACK;
    }

    // Build seats array
    const heroSeat = 0;
    const seats = [];
    for (let i = 0; i < NUM_SEATS; i++) {
      const isBot = i !== heroSeat;
      const archetype = isBot ? BOT_ARCHETYPES[i] : 'hero';
      const name = isBot ? BOT_NAMES[i] : this._heroName;
      const id = isBot ? `bot-${i}` : this._heroId;
      // Position label relative to button
      const relPos = (i - this._buttonSeat + NUM_SEATS) % NUM_SEATS;
      const position = POSITIONS_6MAX[relPos] || `S${i}`;
      seats.push({
        seat: i,
        id,
        name,
        stack: this._stacks[i],
        position,
        is_bot: isBot,
        archetype: isBot ? archetype : undefined,
      });
    }

    // Deal cards
    const deck = buildDeck();
    shuffleFrom(deck, 0);
    let deckIdx = 0;
    const deal = (n) => { const c = deck.slice(deckIdx, deckIdx + n); deckIdx += n; return c; };

    // Deal 2 hole cards to each player in seat order
    const holeCards = [];
    for (let i = 0; i < NUM_SEATS; i++) holeCards.push(deal(2));
    const heroCards = holeCards[heroSeat];

    // SB seat = button+1, BB seat = button+2 (for 6-max)
    const sbSeat = (this._buttonSeat + 1) % NUM_SEATS;
    const bbSeat = (this._buttonSeat + 2) % NUM_SEATS;

    const heroStackBefore = this._stacks[heroSeat];

    // Announce hand_start
    this._ws._receive('hand_start', {
      hand_id: handId,
      button_seat: this._buttonSeat,
      sb: SB,
      bb: BB,
      seats,
      hero_seat: heroSeat,
      hero_cards: heroCards,
      hero_stack_before_blinds: heroStackBefore,
    });

    // Post blinds
    const stacks = [...this._stacks]; // working copy
    const pot = { value: 0 };
    const committed = Array(NUM_SEATS).fill(0); // committed total this hand

    const postBlind = (seat, amount, blindType) => {
      const actual = Math.min(stacks[seat], amount);
      stacks[seat] -= actual;
      committed[seat] += actual;
      pot.value += actual;
      this._ws._receive('post_blind', { seat, amount: actual, blind: blindType });
    };
    postBlind(sbSeat, SB, 'sb');
    postBlind(bbSeat, BB, 'bb');

    // Burn 5 board cards from deck
    const boardCards = deal(5);
    const board = { flop: boardCards.slice(0, 3), turn: [boardCards[3]], river: [boardCards[4]] };

    // State for the hand
    const folded = Array(NUM_SEATS).fill(false);
    const allIn  = Array(NUM_SEATS).fill(false);
    let heroNet = 0;

    // Track who was preflop aggressor (for cbet logic)
    let pfAggressor = bbSeat;

    // ── Betting round ────────────────────────────────────────────────────────

    /**
     * Run one betting street.
     * @param {string}   street     'preflop'|'flop'|'turn'|'river'
     * @param {number}   firstToAct seat index
     * @param {number}   openBet    existing bet (BB for preflop)
     * @returns {string} 'continue'|'end' (end if only 1 active)
     */
    const runStreet = async (street, firstToAct, openBet) => {
      // Street-level committed tracking
      const streetCommitted = Array(NUM_SEATS).fill(0);
      // Pre-populate preflop blinds into street committed
      if (street === 'preflop') {
        streetCommitted[sbSeat] = SB;
        streetCommitted[bbSeat] = BB;
      }

      let currentBet = openBet; // highest bet on this street
      let lastAggressorSeat = street === 'preflop' ? bbSeat : -1;
      let actionsThisRound = 0;

      // Determine active players
      const active = () => seats.filter(s => !folded[s.seat] && !allIn[s.seat]).map(s => s.seat);

      // Action order: starting from firstToAct, wrap around
      let seatOrder = [];
      for (let i = 0; i < NUM_SEATS; i++) {
        seatOrder.push((firstToAct + i) % NUM_SEATS);
      }

      // We iterate until everyone has acted and matched the bet
      // Use a pointer that wraps and stops when action closes
      let actionIdx = 0;
      let lastRaiserIdx = -1; // index in seatOrder of last raiser — action closes when we lap back to them

      // Re-open: when someone raises, we need to revisit everyone before them in this round
      // Easiest: use a "closed" flag per seat, reset on raise
      const acted = Array(NUM_SEATS).fill(false);
      if (street === 'preflop') {
        // Blinds have acted implicitly (but BB can re-open if no raise)
        // We handle this by letting BB check if it gets back to them with no raise
      }

      let loopCount = 0;
      const MAX_LOOPS = NUM_SEATS * 4;

      while (loopCount++ < MAX_LOOPS) {
        const activeSeatList = active();
        if (activeSeatList.length === 0) break;
        if (activeSeatList.length === 1 && allIn.every(a => !a)) {
          // Everyone else folded
          break;
        }

        // Find next seat to act
        const seat = seatOrder[actionIdx % seatOrder.length];
        actionIdx++;

        if (folded[seat] || allIn[seat]) continue;

        const toCall = currentBet - streetCommitted[seat];
        const canCheck = toCall === 0;
        const canCall  = toCall > 0 && stacks[seat] > 0;
        const canFold  = toCall > 0;
        const canBet   = toCall === 0 && stacks[seat] > 0;
        const minRaiseTo = currentBet + Math.max(BB, currentBet); // min re-raise
        const maxRaiseTo = stacks[seat] + streetCommitted[seat]; // all-in
        const canRaise = stacks[seat] > toCall;

        // Check if action has closed (everyone acted and matched)
        // Action closes when all active non-allin players have acted since last aggression
        if (acted[seat] && toCall === 0) {
          // This player already acted and there's no new bet — they're done
          // Check if everyone else is also done
          const allDone = activeSeatList.every(s => acted[s] || folded[s] || allIn[s]);
          if (allDone) break;
          continue; // skip, already acted with no action to face
        }

        this._seq++;

        if (seat === heroSeat) {
          // ── Hero's turn ──────────────────────────────────────────────────
          const legalInfo = {
            can_fold: canFold,
            can_check: canCheck,
            can_call: canCall,
            call_amount: toCall,
            can_bet: canBet,
            can_raise: canRaise,
            min_raise_to: minRaiseTo,
            max_raise_to: maxRaiseTo,
          };
          const currentBoard = street === 'preflop' ? [] :
                               street === 'flop' ? board.flop :
                               street === 'turn' ? [...board.flop, ...board.turn] :
                               [...board.flop, ...board.turn, ...board.river];

          this._ws._receive('action_to_act', {
            hand_id: handId,
            seq: this._seq,
            seat: heroSeat,
            player_id: this._heroId,
            street,
            to_call: toCall,
            pot: pot.value,
            legal: legalInfo,
          });

          // Wait for hero input
          const heroAction = await this._waitHeroAction();
          let { action, amount } = heroAction;

          // Normalize action
          if (action === 'check' && !canCheck) action = 'fold';
          if (action === 'bet' && canCall) action = 'raise'; // treat bet as raise if facing a bet
          amount = parseInt(amount) || 0;

          // Apply action
          let actualAmount = 0;
          if (action === 'fold') {
            folded[heroSeat] = true;
          } else if (action === 'check') {
            // no change
          } else if (action === 'call') {
            actualAmount = Math.min(stacks[heroSeat], toCall);
            stacks[heroSeat] -= actualAmount;
            streetCommitted[heroSeat] += actualAmount;
            committed[heroSeat] += actualAmount;
            pot.value += actualAmount;
            if (stacks[heroSeat] === 0) allIn[heroSeat] = true;
          } else if (action === 'raise' || action === 'bet') {
            // amount is total to raise TO
            const totalToCommit = Math.min(stacks[heroSeat] + streetCommitted[heroSeat], Math.max(amount, minRaiseTo));
            const chipsPut = totalToCommit - streetCommitted[heroSeat];
            stacks[heroSeat] -= chipsPut;
            streetCommitted[heroSeat] = totalToCommit;
            committed[heroSeat] += chipsPut;
            pot.value += chipsPut;
            currentBet = totalToCommit;
            if (stacks[heroSeat] === 0) allIn[heroSeat] = true;
            // Re-open action
            Object.fill ? acted.fill(false) : acted.forEach((_, i) => { acted[i] = false; });
            lastAggressorSeat = heroSeat;
            pfAggressor = heroSeat;
            actualAmount = totalToCommit;
          }

          acted[heroSeat] = true;
          const stackAfter = stacks[heroSeat];
          const commStreet = streetCommitted[heroSeat];

          this._ws._receive('player_action', {
            hand_id: handId,
            seq: this._seq,
            seat: heroSeat,
            player_id: this._heroId,
            street,
            action: folded[heroSeat] ? 'fold' : action,
            amount: actualAmount,
            stack_after: stackAfter,
            committed_street_after: commStreet,
            pot_after: pot.value,
          });

          // Coach tip + rating update
          const currentBoard2 = street === 'preflop' ? [] :
                                street === 'flop' ? board.flop :
                                street === 'turn' ? [...board.flop, ...board.turn] :
                                [...board.flop, ...board.turn, ...board.river];

          const numOpp = active().filter(s => s !== heroSeat).length + (folded.filter(Boolean).length > 0 ? 0 : 0);
          const coachPayload = computeCoach(
            heroCards, currentBoard2, pot.value, toCall,
            stacks[heroSeat], Math.max(1, numOpp), action, handId, this._seq
          );
          this._ws._receive('coach_tip', coachPayload);
          // Store for Ask-coach Q&A
          this._ws._lastCoachCtx = {
            holeCards: heroCards,
            board: currentBoard2,
            pot: pot.value,
            toCall,
            equity: coachPayload.math.equity,
            alpha: coachPayload.math.alpha,
            edge: coachPayload.math.edge,
            outs: coachPayload.math.outs,
            verdict: coachPayload.math.verdict,
            ev_call: coachPayload.math.ev_by_action.call,
            ev_raise: coachPayload.math.ev_by_action.raise,
            spot: coachPayload.spot,
          };

          // Rating: pick ev for chosen action vs best
          const math = coachPayload.math;
          const actionEvMap = {
            fold:    0,
            check:   math.ev_by_action.check ?? 0,
            call:    math.ev_by_action.call  ?? 0,
            bet:     math.ev_by_action.bet   ?? 0,
            raise:   math.ev_by_action.raise ?? 0,
            all_in:  math.ev_by_action.raise ?? 0,
          };
          const userEv = actionEvMap[action] ?? 0;
          const bestAction = math.verdict;
          const bestEv = actionEvMap[bestAction] ?? 0;
          const ratingUpdate = this._rating.update(userEv, bestEv, BB);
          ratingUpdate.ideal_action = bestAction;
          ratingUpdate.user_action = action;
          this._ws._receive('rating_update', ratingUpdate);

        } else {
          // ── Bot's turn ───────────────────────────────────────────────────
          const botThink = randInt(400, 800);
          await delay(botThink);

          const currentBoard = street === 'preflop' ? [] :
                               street === 'flop' ? board.flop :
                               street === 'turn' ? [...board.flop, ...board.turn] :
                               [...board.flop, ...board.turn, ...board.river];

          const botHole = holeCards[seat];
          const numOpp = active().filter(s => s !== seat).length;
          const strength = await estimateHandStrength(botHole, currentBoard, Math.max(1, numOpp));

          const decision = botDecide(
            BOT_ARCHETYPES[seat],
            strength,
            pot.value,
            toCall,
            minRaiseTo,
            stacks[seat],
            street,
            pfAggressor === seat
          );

          let action = decision.action;
          let amount = decision.amount || 0;
          let actualAmount = 0;

          if (action === 'fold') {
            folded[seat] = true;
          } else if (action === 'check') {
            // no chips
          } else if (action === 'call') {
            actualAmount = Math.min(stacks[seat], toCall);
            stacks[seat] -= actualAmount;
            streetCommitted[seat] += actualAmount;
            committed[seat] += actualAmount;
            pot.value += actualAmount;
            if (stacks[seat] === 0) allIn[seat] = true;
          } else if (action === 'raise' || action === 'bet') {
            const totalToCommit = Math.min(stacks[seat] + streetCommitted[seat], Math.max(amount, minRaiseTo));
            const chipsPut = totalToCommit - streetCommitted[seat];
            stacks[seat] -= chipsPut;
            streetCommitted[seat] = totalToCommit;
            committed[seat] += chipsPut;
            pot.value += chipsPut;
            currentBet = totalToCommit;
            if (stacks[seat] === 0) allIn[seat] = true;
            // Re-open action
            for (let k = 0; k < NUM_SEATS; k++) acted[k] = false;
            lastAggressorSeat = seat;
            pfAggressor = seat;
            actualAmount = totalToCommit;
          }

          acted[seat] = true;

          this._ws._receive('player_action', {
            hand_id: handId,
            seq: this._seq,
            seat,
            player_id: seats[seat].id,
            street,
            action: folded[seat] ? 'fold' : action,
            amount: actualAmount,
            stack_after: stacks[seat],
            committed_street_after: streetCommitted[seat],
            pot_after: pot.value,
          });
        }

        // Check if only 0 or 1 active players remain (everyone else folded/allin)
        const nowActive = active();
        if (nowActive.length === 0) break;
        if (nowActive.length === 1 && active().every(s => allIn[s] || s === nowActive[0])) {
          // Could still need to run out board
          break;
        }

        // Check if action should close: all active players have acted and matched bet
        const allMatched = seats.every(s =>
          folded[s.seat] || allIn[s.seat] ||
          (acted[s.seat] && streetCommitted[s.seat] === currentBet)
        );
        if (allMatched) break;
      }

      // Count non-folded
      const remaining = seats.filter(s => !folded[s.seat]);
      return remaining.length > 1 ? 'continue' : 'end';
    };

    // ── Preflop ────────────────────────────────────────────────────────────
    // UTG is 3 seats left of button
    const utgSeat = (this._buttonSeat + 3) % NUM_SEATS;
    const pfResult = await runStreet('preflop', utgSeat, BB);

    // ── Flop ───────────────────────────────────────────────────────────────
    if (pfResult === 'continue') {
      const flopBoard = board.flop;
      this._ws._receive('board', {
        hand_id: handId, street: 'flop',
        new_cards: flopBoard, board: flopBoard, pot: pot.value,
      });
      const postFlopFirst = (this._buttonSeat + 1) % NUM_SEATS; // SB or first active left of button
      const flopResult = await runStreet('flop', postFlopFirst, 0);

      // ── Turn ─────────────────────────────────────────────────────────────
      if (flopResult === 'continue') {
        const turnBoard = [...flopBoard, ...board.turn];
        this._ws._receive('board', {
          hand_id: handId, street: 'turn',
          new_cards: board.turn, board: turnBoard, pot: pot.value,
        });
        const turnResult = await runStreet('turn', postFlopFirst, 0);

        // ── River ───────────────────────────────────────────────────────────
        if (turnResult === 'continue') {
          const riverBoard = [...turnBoard, ...board.river];
          this._ws._receive('board', {
            hand_id: handId, street: 'river',
            new_cards: board.river, board: riverBoard, pot: pot.value,
          });
          await runStreet('river', postFlopFirst, 0);
        }
      }
    }

    // ── Showdown / Award pot ───────────────────────────────────────────────
    const finalBoard = [...board.flop, ...board.turn, ...board.river];
    const activeFinal = seats.filter(s => !folded[s.seat]);

    let winners = [];
    let showdown = [];

    if (activeFinal.length === 1) {
      // Uncontested pot
      const winner = activeFinal[0];
      stacks[winner.seat] += pot.value;
      winners = [{ player_id: winner.id, amount: pot.value, hand_desc: 'Wins uncontested' }];
    } else {
      // Evaluate hands
      const scores = activeFinal.map(s => ({
        seat: s.seat,
        id: s.id,
        score: bestHand7(holeCards[s.seat], finalBoard),
        cards: holeCards[s.seat],
      }));
      scores.forEach(s => {
        showdown.push({ player_id: s.id, cards: s.cards, hand_desc: describeScore(s.score) });
      });
      // Find best score
      let best = scores[0].score;
      for (const s of scores) {
        if (compareScores(s.score, best) > 0) best = s.score;
      }
      const winnerSeats = scores.filter(s => compareScores(s.score, best) === 0);
      const share = Math.floor(pot.value / winnerSeats.length);
      for (const w of winnerSeats) {
        stacks[w.seat] += share;
        winners.push({ player_id: w.id, amount: share, hand_desc: describeScore(w.score) });
      }
      // Remainder chips to first winner (rounding)
      const rem = pot.value - share * winnerSeats.length;
      if (rem > 0) stacks[winnerSeats[0].seat] += rem;
    }

    // Hero net
    const heroStackAfter = stacks[heroSeat];
    heroNet = heroStackAfter - heroStackBefore;

    // Persist stacks
    for (let i = 0; i < NUM_SEATS; i++) this._stacks[i] = stacks[i];

    this._ws._receive('hand_end', {
      hand_id: handId,
      winners,
      showdown,
      final_board: finalBoard,
      hero_net: heroNet,
      hero_stack_after: heroStackAfter,
    });

    // Rotate button
    this._buttonSeat = (this._buttonSeat + 1) % NUM_SEATS;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 10. ASK-COACH RESPONSE GENERATOR
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Generate a plain-English coaching answer based on the question text and the
 * last known hand context (equity, pot odds, verdict, etc.).
 *
 * @param {string} question  - raw question from user
 * @param {object|null} ctx  - _lastCoachCtx from the MockWebSocket
 * @returns {string}
 */
function generateCoachAnswer(question, ctx) {
  const q = (question || '').toLowerCase();

  // No hand in progress
  if (!ctx) {
    return 'Play a hand first — once you face a decision I\'ll have the math to explain.';
  }

  const { equity, alpha, edge, outs, verdict, ev_call, ev_raise, toCall, pot, spot, holeCards, board } = ctx;
  const eqPct   = (equity * 100).toFixed(0);
  const oddsPct = (alpha  * 100).toFixed(0);
  const edgePct = (Math.abs(edge) * 100).toFixed(0);
  const spotName = spot || 'preflop';

  // ── Pattern matching ───────────────────────────────────────────────────────

  // "Why is that the best play?" / "why that" / "explain"
  if (/why|best play|explain|right call|correct/.test(q)) {
    if (verdict === 'fold') {
      return `Fold is best here because your equity (~${eqPct}%) is below the ${oddsPct}% needed to break even on a call. You\'re paying more for your cards than they\'re worth — folding saves chips.`;
    }
    if (verdict === 'call') {
      return `Call is best because your equity (~${eqPct}%) clears the ${oddsPct}% pot-odds threshold by ${edgePct} percentage points. You\'re getting a slightly better price than the risk warrants, so calling has positive expected value.`;
    }
    if (verdict === 'raise') {
      return `Raise is best because your equity (~${eqPct}%) is ${edgePct}pp above the pot-odds requirement (${oddsPct}%). With that edge, building the pot with a raise extracts more value than a flat call.`;
    }
    if (verdict === 'bet') {
      return `Bet is best because you\'re a slight favourite with ~${eqPct}% equity and no bet to face. Betting builds the pot and denies free cards to hands that might beat you later.`;
    }
    if (verdict === 'check') {
      return `Check is best here — your equity (~${eqPct}%) is below 54%, meaning you\'re not a strong enough favourite to bet for value. A bet would only get called by hands that beat you.`;
    }
  }

  // "What if I fold?" / "ev of folding"
  if (/fold|give up|muck/.test(q)) {
    if (verdict === 'fold') {
      return `Folding is actually the recommendation here. Your equity (~${eqPct}%) is below the ${oddsPct}% pot odds required. Folding has 0 EV, while calling has negative EV of roughly ${(ev_call).toFixed(1)} chips — so you\'re saving money.`;
    }
    return `If you fold here, you walk away with 0 chips from this pot. Your equity is ~${eqPct}% and pot odds require ${oddsPct}% — so calling or raising has positive EV. Folding surrenders that edge.`;
  }

  // "What hands beat me?" / "what beats"
  if (/beat|ahead|behind|what.*hand/.test(q)) {
    const boardStr = board && board.length ? board.join(' ') : '(no board)';
    if (equity >= 0.65) {
      return `With ~${eqPct}% equity on the ${spotName} (board: ${boardStr}), you\'re ahead of most opponent hands. Only the top of their range — sets, two pair, strong draws — has you beat or close. Your hand is strong here.`;
    }
    if (equity >= 0.45) {
      return `You\'re roughly even (~${eqPct}%) on the ${spotName}. The board (${boardStr}) still leaves room for opponents to hold better made hands or strong draws. Proceed carefully.`;
    }
    return `At ~${eqPct}% equity, more of your opponent\'s likely hands beat you than don\'t. On the ${spotName} (board: ${boardStr}), you\'re in a tough spot — hence the ${verdict} recommendation.`;
  }

  // "Pot odds" / "odds" / "how much do I need"
  if (/pot.?odds|odds|how much equity|break.?even/.test(q)) {
    if (toCall > 0) {
      return `You must call ${toCall} chips into a pot of ${pot}. That\'s ${toCall} ÷ ${pot + toCall} = ${oddsPct}% pot odds. Your equity (~${eqPct}%) ${equity >= alpha ? 'exceeds' : 'falls short of'} that threshold by ${edgePct} percentage points.`;
    }
    return `You\'re not facing a bet right now — it\'s a check-or-bet spot. Your equity is ~${eqPct}%. The ${verdict} recommendation is based on whether you\'re a favourite to win the hand.`;
  }

  // "Raise" / "why not raise" / "should I raise"
  if (/raise|re.?raise|3.?bet/.test(q)) {
    if (verdict === 'raise') {
      return `Raising is correct here. Your equity (~${eqPct}%) is far above the pot-odds requirement (${oddsPct}%), giving you a ${edgePct}pp edge. Raising builds the pot while you\'re ahead.`;
    }
    return `Raising isn\'t recommended here because your equity (~${eqPct}%) only gives you a ${edgePct}pp edge over the required ${oddsPct}%. A call or fold is more appropriate — raising risks more chips than the edge justifies.`;
  }

  // "Outs" / "drawing"
  if (/out|draw|miss|improve/.test(q)) {
    if (outs > 0) {
      const riverChance = (outs * (board.length === 3 ? 4 : 2)).toFixed(0);
      return `You have ${outs} outs to improve your hand. Using the Rule of ${board.length === 3 ? '4' : '2'}, that\'s roughly ${riverChance}% chance of hitting. Your total equity including the made-hand component is ~${eqPct}%.`;
    }
    return `Your hand doesn\'t have clear drawing outs right now — your ~${eqPct}% equity comes from your made hand, not draws.`;
  }

  // Generic fallback
  return `On the ${spotName}: your equity is ~${eqPct}%, pot odds require ${oddsPct}%, giving you a ${edge >= 0 ? '+' : ''}${edgePct}pp edge. The recommended play is ${verdict.toUpperCase()}. Ask about "pot odds", "fold EV", "what beats me", or "why raise" for more detail.`;
}

// ─────────────────────────────────────────────────────────────────────────────
// 11. MOCK WEBSOCKET
// ─────────────────────────────────────────────────────────────────────────────

class MockWebSocket extends EventTarget {
  // WebSocket ready-state constants
  CONNECTING = 0;
  OPEN = 1;
  CLOSING = 2;
  CLOSED = 3;

  readyState = 1; // always OPEN

  constructor(url) {
    super();
    this._url = url;
    this._engine = new FeltEngine(this);

    // Emit open event asynchronously so app.js can attach listeners first
    Promise.resolve().then(() => {
      const openEvent = new Event('open');
      this.dispatchEvent(openEvent);
      if (typeof this.onopen === 'function') this.onopen(openEvent);
    });
  }

  /**
   * Called by app.js to send a message to the "server".
   */
  send(rawJson) {
    let msg;
    try {
      msg = JSON.parse(rawJson);
    } catch (e) {
      console.warn('[MockWebSocket] Could not parse:', rawJson);
      return;
    }
    const { type, data } = msg;
    switch (type) {
      case 'join':
        this._engine.onJoin(data || {});
        break;
      case 'action':
        this._engine.onHeroAction(data || {});
        break;
      case 'next_hand':
        this._engine.onNextHand();
        break;
      case 'ping':
        // Respond with pong immediately
        this._receive('pong', { ts: Date.now() });
        break;
      case 'q':
      case 'ask_coach': {
        const answer = generateCoachAnswer((data || {}).question || '', this._lastCoachCtx);
        // Small delay so the "thinking…" message is visible briefly
        setTimeout(() => this._receive('coach_answer', { answer }), 600);
        break;
      }
      case 'start_drill': {
        const lessonId = (data || {}).lesson_id;
        const question = pickDrillQuestion(lessonId);
        if (question) {
          this._receive('drill_question', question);
        } else {
          this._receive('drill_question', {
            question: `No questions found for lesson "${lessonId}". Check the lesson catalog.`,
            answer_type: 'mc',
            choices: ['OK'],
          });
        }
        break;
      }
      case 'submit_drill_answer': {
        const feedback = gradeDrillAnswer((data || {}).answer);
        this._receive('drill_feedback', feedback);
        break;
      }
      case 'set_active_lesson':
        // Acknowledged — no server state needed client-side
        break;
      default:
        // Unknown message type — ignore silently
        break;
    }
  }

  /**
   * Called by the engine to push a message to app.js.
   */
  _receive(type, data) {
    const raw = JSON.stringify({ type, data });
    const event = new MessageEvent('message', { data: raw });
    this.dispatchEvent(event);
    if (typeof this.onmessage === 'function') this.onmessage(event);
  }

  close() {
    this.readyState = this.CLOSED;
    const closeEvent = new CloseEvent('close', { wasClean: true, code: 1000 });
    this.dispatchEvent(closeEvent);
    if (typeof this.onclose === 'function') this.onclose(closeEvent);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 11. INSTALL OVERRIDE
// ─────────────────────────────────────────────────────────────────────────────

// Expose constants that app.js may check against ws.readyState
MockWebSocket.CONNECTING = 0;
MockWebSocket.OPEN       = 1;
MockWebSocket.CLOSING    = 2;
MockWebSocket.CLOSED     = 3;

// Replace the global WebSocket constructor
window.WebSocket = MockWebSocket;

console.info('[engine-ws.js] MockWebSocket installed — running game engine client-side.');
