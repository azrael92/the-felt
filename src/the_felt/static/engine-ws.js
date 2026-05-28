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
      if (url.includes('/api/lessons'))       return jsonResponse({ modules: [] });
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
  if (board.length >= 5) {
    // Board complete — exact equity
    const heroScore = bestHand7(holeCards, board);
    // We don't know opponent cards here, return 0.5 as default
    return 0.5;
  }
  const known = new Set([...holeCards, ...board]);
  const remaining = buildDeck().filter(c => !known.has(c));

  let wins = 0, ties = 0;
  const needed = 5 - board.length;

  for (let i = 0; i < iters; i++) {
    // Partial Fisher-Yates to pick cards we need
    const deck = [...remaining];
    const totalNeeded = needed + numOpponents * 2;
    for (let j = 0; j < totalNeeded; j++) {
      const r = j + Math.floor(Math.random() * (deck.length - j));
      [deck[j], deck[r]] = [deck[r], deck[j]];
    }
    const simBoard = [...board, ...deck.slice(0, needed)];
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
  if (!flushDraw) { // avoid double-counting OESD+flush draw
    if (oesd) outs += 8;
    else if (gutshot) outs += 4;
  } else {
    if (oesd) outs += 8;
    else if (gutshot) outs += 4;
  }

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
 */
function computeCoach(holeCards, board, pot, callAmount, heroStack, numOpponents, userAction, handId, seq) {
  const equity = calcEquity(holeCards, board, numOpponents, 400);
  const alpha = callAmount > 0 ? callAmount / (pot + callAmount) : 0;
  const mdf = 1 - alpha;
  const edge = equity - alpha;
  const outs = estimateOuts(holeCards, board);
  const cardsLeft47 = Math.max(47 - board.length * 2, 1); // rough
  const next_card_pct = outs / 47;
  const by_river_pct = board.length === 3 ? 1 - Math.pow(1 - outs / 47, 2) :
                       board.length === 4 ? outs / 46 : 0;

  // EV calculations
  const ev_fold = 0;
  const ev_call = callAmount > 0
    ? equity * (pot + callAmount) - (1 - equity) * callAmount
    : equity * pot;
  // Rough raise EV: assume raising 2.5x pot, opponent folds fold_freq
  const raiseAmt = Math.min(heroStack, pot * 2.5);
  const oppFoldFreq = 0.45;
  const ev_raise = oppFoldFreq * pot + (1 - oppFoldFreq) * (equity * (pot + raiseAmt) - (1 - equity) * raiseAmt);

  // Verdict
  let verdict;
  if (callAmount === 0) {
    verdict = equity > 0.35 ? 'check' : 'check';
  } else if (equity >= alpha + 0.05) {
    verdict = ev_raise > ev_call ? 'raise' : 'call';
  } else if (equity >= alpha - 0.02) {
    verdict = 'call';
  } else {
    verdict = 'fold';
  }

  const verdictMap = { fold: 'Fold', check: 'Check', call: 'Call', raise: 'Raise' };

  // Notes
  const notes = [];
  if (outs > 0) notes.push(`${outs} outs to improve`);
  if (equity > 0.6) notes.push('Strong equity vs range');
  else if (equity < 0.3) notes.push('Behind range – proceed cautiously');
  if (edge > 0.1) notes.push('Profitable call / bet spot');
  if (edge < -0.1) notes.push('Folding preserves EV');
  if (mdf < 0.4) notes.push(`Low MDF: you only need to defend ${(mdf * 100).toFixed(0)}% of range`);

  const spot = board.length === 0 ? 'preflop' :
               board.length === 3 ? 'flop' :
               board.length === 4 ? 'turn' : 'river';

  return {
    hand_id: handId,
    seq,
    tier: 1,
    to_call: callAmount,
    spot,
    math: {
      equity: +equity.toFixed(3),
      pot_odds_required: +alpha.toFixed(3),
      edge: +edge.toFixed(3),
      mdf: +mdf.toFixed(3),
      alpha: +alpha.toFixed(3),
      outs,
      next_card_pct: +next_card_pct.toFixed(3),
      by_river_pct: +by_river_pct.toFixed(3),
      ev_by_action: { fold: 0, call: +ev_call.toFixed(2), raise: +ev_raise.toFixed(2) },
      ev_labels: { fold: 'Fold', call: callAmount > 0 ? 'Call' : 'Check', raise: 'Raise' },
      verdict,
      verdict_label: verdictMap[verdict] || verdict,
      verdict_button: verdictMap[verdict] || verdict,
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
// 8. SIMPLIFIED GLICKO-2 TRACKER
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

          // Rating: pick ev for chosen action vs best
          const math = coachPayload.math;
          const actionEvMap = { fold: 0, check: 0, call: math.ev_by_action.call, raise: math.ev_by_action.raise, bet: math.ev_by_action.raise };
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
// 10. MOCK WEBSOCKET
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
        // Coach question — not implemented, silently ignore
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
