// the_felt — main client.
// Avoid top-level const collisions with cards.js (it also declares CARD_W).

const _SC = window.SmokerCards;
const sk = {
  svgEl: _SC.svgEl,
  cardFront: _SC.cardFront,
  cardBack: _SC.cardBack,
  cardSlot: _SC.cardSlot,
  chipStack: _SC.chipStack,
  defs: _SC.defs,
  W: _SC.CARD_W,
  H: _SC.CARD_H,
};

// =============================================================
// Curriculum state
// =============================================================
const curriculum = {
  modules: [],        // loaded from GET /api/lessons
  progress: {},       // { lesson_id: {state, attempts, correct, recent_accuracy} }
  activeLesson: null,
  recommendedLessonId: null,
  pendingErrorDrill: null,  // {lesson_id, kind} to surface at start of next hand
};

// =============================================================
// State
// =============================================================
const state = {
  ws: null,
  userId: null,
  seats: [],
  liveSeats: [],
  buttonSeat: 0,
  heroSeat: 0,
  heroCards: [],
  board: [],
  pot: 0,
  street: 'preflop',
  bb: 10,
  sb: 5,
  handCounter: 0,
  toActSeat: -1,
  toActReq: null,
  legal: null,
  awaitingUser: false,
  handComplete: false,
  currentTier: 1,
  recommendedButton: null,
  lastCoachMath: null,
  lastCoachTip: null,
  lastPolish: '',
  lastRating: null,
  // Study mode: hide recommendation pre-action, force user to decide
  studyMode: false,
  // Pilot-style training
  stageId: 1,
  stageTitle: 'Read your hand',
  pendingQuiz: null,        // active quiz payload
  quizAnswers: {},          // map question id -> submitted value (or '__revealed__')
  freqLabel: 'every turn',
  streakInStage: 0,
  // Session
  heroStackBeforeBlinds: 1000,
  netChips: 0,
  handsWon: 0,
  bestPlayCount: 0,
  bestPlayTotal: 0,
  // Per-decision tracking for recap
  thisHandDecisions: [],
  // Chat
  chatHistory: [],
};

window.__state = state;

// =============================================================
// DOM helpers
// =============================================================
const $ = (id) => document.getElementById(id);

function setStatus(text, kind = '') {
  const el = $('status');
  el.textContent = text;
  el.className = `status ${kind}`;
}

// =============================================================
// WebSocket (with auto-reconnect + exponential backoff)
// =============================================================
let reconnectAttempts = 0;
let reconnectTimer = null;

function connect() {
  // Don't double-connect
  if (state.ws && state.ws.readyState === WebSocket.CONNECTING) return;
  if (state.ws && state.ws.readyState === WebSocket.OPEN) return;

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const url = `${proto}://${location.host}/ws`;
  setStatus('connecting…');
  const ws = new WebSocket(url);
  state.ws = ws;

  ws.addEventListener('open', () => {
    setStatus('connected', 'ok');
    reconnectAttempts = 0;
    ws.send(JSON.stringify({
      type: 'join',
      v: 1,
      data: { user_name: 'You', seats: 6, stack_bb: 100, sb: 5, bb: 10 },
    }));
  });

  ws.addEventListener('close', (e) => {
    setStatus('reconnecting…', 'err');
    scheduleReconnect();
  });

  ws.addEventListener('error', () => {
    setStatus('connection error', 'err');
  });

  ws.addEventListener('message', (e) => {
    try {
      handle(JSON.parse(e.data));
    } catch (err) {
      console.error('bad message', e.data, err);
    }
  });
}

function scheduleReconnect() {
  if (reconnectTimer) return;  // already scheduled
  const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 8000);
  reconnectAttempts++;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, delay);
}

// Reconnect when the browser tab regains focus (covers laptop-sleep cases)
window.addEventListener('focus', () => {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    reconnectAttempts = 0;  // user-initiated, retry quickly
    connect();
  }
});

function send(type, data) {
  if (state.ws && state.ws.readyState === 1) {
    state.ws.send(JSON.stringify({ type, v: 1, data }));
  }
}

// =============================================================
// Message dispatch
// =============================================================
function handle(msg) {
  const { type, data } = msg;
  switch (type) {
    case 'joined': onJoined(data); break;
    case 'hand_start': onHandStart(data); break;
    case 'post_blind': onPostBlind(data); break;
    case 'player_action': onPlayerAction(data); break;
    case 'board': onBoard(data); break;
    case 'action_to_act': onActionToAct(data); break;
    case 'coach_tip': onCoachTip(data); break;
    case 'coach_tip_polish': onCoachTipPolish(data); break;
    case 'coach_answer': onCoachAnswer(data); break;
    case 'coach_narration': onCoachNarration(data); break;
    case 'drill_question': onDrillQuestion(data); break;
    case 'drill_feedback': onDrillFeedback(data); break;
    case 'stage_quiz': onStageQuiz(data); break;
    case 'stage_quiz_feedback': onStageQuizFeedback(data); break;
    case 'stage_change': onStageChange(data); break;
    case 'rating_update': onRatingUpdate(data); break;
    case 'leak_report': onLeakReport(data); break;
    case 'difficulty_update': onDifficultyUpdate(data); break;
    case 'tier_update': onTierUpdate(data); break;
    case 'hand_end': onHandEnd(data); break;
    case 'error': onError(data); break;
    case 'pong': break;
    default: console.log('unhandled', type, data);
  }
}

// =============================================================
// Handlers
// =============================================================
function onJoined(data) {
  state.userId = data.user_id;
  setStatus('joined', 'ok');
  // Once we have a user_id, fetch their lesson progress
  loadProgress();
}

function onHandStart(data) {
  state.handCounter++;
  $('hand-counter').textContent = state.handCounter;
  state.seats = data.seats;
  state.liveSeats = data.seats.map((s) => ({ ...s, bet: 0, folded: false, lastAction: null }));
  state.buttonSeat = data.button_seat;
  state.heroSeat = data.hero_seat;
  state.heroCards = data.hero_cards;
  state.heroStackBeforeBlinds = data.hero_stack_before_blinds || 1000;
  state.board = [];
  state.pot = 0;
  state.street = 'preflop';
  state.bb = data.bb;
  state.sb = data.sb;
  state.handComplete = false;
  state.toActSeat = -1;
  state.toActReq = null;
  state.awaitingUser = false;
  state.thisHandDecisions = [];
  $('recap-overlay').hidden = true;
  $('action-bar').hidden = true;
  $('coach-content').hidden = true;
  $('coach-empty').hidden = false;
  $('post-action-feedback').hidden = true;
  clearLog();
  logEvent(`Hand ${state.handCounter} dealt`, 'hand-start');
  render();
}

function onPostBlind(data) {
  const seat = state.liveSeats[data.seat];
  if (seat) {
    seat.bet = (seat.bet || 0) + data.amount;
    seat.stack -= data.amount;
  }
  state.pot += data.amount;
  const which = data.blind === 'sb' ? 'small blind' : 'big blind';
  logEvent(`${seatName(data.seat)} posts ${which} <strong>${data.amount}</strong>`);
  render();
}

function onPlayerAction(data) {
  const seat = state.liveSeats[data.seat];
  if (seat) {
    seat.stack = data.stack_after;
    seat.bet = data.committed_street_after;
    if (data.action === 'fold') seat.folded = true;
    seat.lastAction = data.action === 'fold' ? 'folded'
      : data.action === 'check' ? 'checked'
      : data.action === 'call' ? `called ${data.amount}`
      : data.action === 'bet' ? `bet ${data.amount}`
      : data.action === 'raise' ? `raised to ${data.amount}`
      : data.action === 'all_in' ? `all-in ${data.amount}`
      : data.action;
  }
  state.pot = data.pot_after;
  const isAggression = ['bet', 'raise', 'all_in'].includes(data.action);
  const verb = describeAction(data.action, data.amount);
  logEvent(`${seatName(data.seat)} ${verb}`, isAggression ? 'aggression' : null);
  state.toActSeat = -1;
  render();
}

function describeAction(a, amount) {
  switch (a) {
    case 'fold':   return 'folds';
    case 'check':  return 'checks';
    case 'call':   return `calls ${amount}`;
    case 'bet':    return `bets <strong>${amount}</strong>`;
    case 'raise':  return `raises to <strong>${amount}</strong>`;
    case 'all_in': return `goes <strong>all-in for ${amount}</strong>`;
    default: return a;
  }
}

function onBoard(data) {
  state.board = data.board;
  state.pot = data.pot;
  state.street = data.street;
  for (const s of state.liveSeats) s.bet = 0;
  const STREET_LABEL_T1 = { flop: '🂠 Flop', turn: '🂠 Turn', river: '🂠 River' };
  const lbl = state.currentTier <= 1
    ? (STREET_LABEL_T1[data.street] || data.street)
    : data.street;
  logEvent(`${lbl} — ${data.new_cards.join(' · ')}`, 'street');
  render();
}

function onActionToAct(data) {
  state.toActSeat = data.seat;
  state.toActReq = data;
  state.legal = data.legal;
  state.awaitingUser = (data.seat === state.heroSeat);
  render();
  if (state.awaitingUser) {
    showActionBar(data);
  } else {
    $('action-bar').hidden = true;
  }
}

function onCoachTip(data) {
  state.lastCoachTip = data;
  state.lastCoachMath = data.math;
  state.currentTier = data.tier;
  $('coach-empty').hidden = true;
  $('coach-content').hidden = false;
  $('post-action-feedback').hidden = true;

  // Concept-focus banner: if server included a hint, show it above coach content
  const focus = data.concept_focus;
  const focusEl = $('concept-focus');
  if (focus && focus.hint) {
    focusEl.innerHTML = `📚 <strong>${focus.module_title || focus.module_id}</strong> · ${focus.hint}`;
    focusEl.hidden = false;
  } else {
    focusEl.hidden = true;
  }

  // Spot chip — leading signal
  const chip = $('spot-chip');
  if (data.spot) {
    chip.className = `spot-chip spot-${data.spot}`;
    chip.textContent = state.studyMode ? '🤔 What kind of spot is this?' : humanizeSpot(data.spot);
    chip.style.display = '';
  } else {
    chip.style.display = 'none';
  }

  // Verdict — hidden in study mode (user must decide first)
  const verdict = data.math.verdict_label || data.math.verdict;
  const verdictEl = $('coach-verdict');
  if (state.studyMode) {
    // Lead with the QUESTION, not the answer
    verdictEl.innerHTML = `<span style="color:var(--text-dim)">Your move — math is below, decide before clicking.</span>`;
  } else {
    verdictEl.innerHTML = `Best play: <span class="verdict-action">${verdict}</span>`;
  }

  // One-line "why" — derived from math first; polish replaces it when it arrives.
  // In study mode we show ONLY the math facts, never the recommended action.
  const tierStrings = data.tier_strings || {};
  let why;
  if (state.studyMode) {
    // Math-first prompt, no verdict
    const pct = (data.math.equity * 100).toFixed(0);
    if (data.to_call > 0) {
      const need = (data.math.pot_odds_required * 100).toFixed(0);
      why = `Your hand wins ~${pct}% of runouts. You need ${need}% to break even on a call. ` +
            `What's your move?`;
    } else {
      why = `Your hand wins ~${pct}% of runouts. Nobody has bet yet. What's your move?`;
    }
  } else if (data.tier <= 1) {
    const pct = (data.math.equity * 100).toFixed(0);
    why = `${pct}% chance to win. Best play is ${(data.math.verdict_label || data.math.verdict).toLowerCase()}.`;
  } else {
    why = `Equity ${(data.math.equity * 100).toFixed(0)}% vs required ${(data.math.pot_odds_required * 100).toFixed(0)}%. ${data.math.verdict_label || data.math.verdict}.`;
  }
  $('coach-why').textContent = why;
  $('coach-why').dataset.placeholder = 'true';

  // Villain line
  const vill = $('coach-villain');
  if (data.villain_archetype) {
    const desc = data.tier <= 1
      ? humanizeArchetype(data.villain_archetype)
      : `${data.villain_archetype} (${data.villain_range_size} combos)`;
    vill.innerHTML = `Up against: <strong>${desc}</strong>`;
    vill.style.display = '';
  } else {
    vill.style.display = 'none';
  }

  // Math drawer values
  const m = data.math;
  const eqPct = (m.equity * 100).toFixed(1);
  $('equity-num').textContent = `${eqPct}%`;
  $('equity-bar').style.width = `${eqPct}%`;
  const oddsPct = (m.pot_odds_required * 100).toFixed(1);
  $('odds-num').textContent = m.pot_odds_required > 0 ? `${oddsPct}%` : '—';
  $('odds-bar').style.width = `${oddsPct}%`;
  $('equity-label').textContent = data.tier <= 1 ? 'Win chance' : 'Equity';
  $('odds-label').textContent = data.tier <= 1 ? 'Win % needed' : 'Pot odds';

  $('edge-num').textContent = (m.edge * 100).toFixed(0) + '%';
  $('edge-num').style.color = m.edge >= 0 ? 'var(--good)' : 'var(--danger)';
  $('mdf-num').textContent = (m.mdf * 100).toFixed(0) + '%';
  $('alpha-num').textContent = (m.alpha * 100).toFixed(0) + '%';
  $('outs-num').textContent = m.outs > 0 ? m.outs : '—';
  $('nextc-num').textContent = m.next_card_pct > 0 ? `${(m.next_card_pct * 100).toFixed(0)}%` : '—';
  $('byriver-num').textContent = m.by_river_pct > 0 ? `${(m.by_river_pct * 100).toFixed(0)}%` : '—';
  $('math-advanced').hidden = (data.tier <= 1);

  // EV list with human labels
  const ul = $('ev-list');
  ul.innerHTML = '';
  const labels = m.ev_labels || {};
  const entries = Object.entries(m.ev_by_action).sort((a, b) => b[1] - a[1]);
  for (const [name, ev] of entries) {
    const li = document.createElement('li');
    const human = labels[name] || name;
    li.innerHTML = `<span>${human}</span><span>${ev >= 0 ? '+' : ''}${ev.toFixed(1)}</span>`;
    if (name === m.verdict) li.classList.add('best');
    if (ev < -5) li.classList.add('bad');
    ul.appendChild(li);
  }

  // Tier notes (only the deeper ones; we already show spot lesson elsewhere)
  const notesUl = $('tier-notes');
  notesUl.innerHTML = '';
  const SKIP = new Set([
    'pot_odds', 'equity_vs_required', 'equity', 'outs', 'ev', 'verdict',
    'price_to_call', 'win_chance', 'cards_that_help', 'spot_lesson',
  ]);
  for (const [k, v] of Object.entries(tierStrings)) {
    if (SKIP.has(k)) continue;
    const li = document.createElement('li');
    const label = labelMap(k);
    li.innerHTML = `<span class="label">${label}</span> ${v}`;
    notesUl.appendChild(li);
  }

  // ---- Math derivation (the actual teaching) ----
  renderDerivation(data.derivation || []);

  // Update the recommended button highlight
  state.recommendedButton = m.verdict_button || null;
  highlightRecommendedButton();
}

function renderDerivation(steps) {
  const ol = $('derivation-steps');
  ol.innerHTML = '';
  if (!steps || !steps.length) return;
  for (let i = 0; i < steps.length; i++) {
    const s = steps[i];
    const li = document.createElement('li');
    li.className = 'deriv-step';
    if (state.studyMode) li.classList.add('locked');
    li.dataset.idx = i;
    li.innerHTML = `
      <div class="deriv-head">
        <span class="deriv-num">${i + 1}</span>
        <span class="deriv-label">${escapeHtml(s.label)}</span>
        ${state.studyMode ? `<button class="deriv-reveal">reveal ▾</button>` : ''}
      </div>
      <div class="deriv-body">
        <div class="deriv-q">${escapeHtml(s.q)}</div>
        <div class="deriv-formula"><span class="lbl">Formula</span><code>${escapeHtml(s.formula)}</code></div>
        <div class="deriv-numbers"><span class="lbl">Plug in</span><code>${escapeHtml(s.numbers)}</code></div>
        <div class="deriv-result">→ <strong>${escapeHtml(s.result)}</strong></div>
        <div class="deriv-gloss">${escapeHtml(s.gloss)}</div>
      </div>
    `;
    ol.appendChild(li);
  }
  // Wire reveal buttons
  if (state.studyMode) {
    ol.querySelectorAll('.deriv-reveal').forEach((btn) => {
      btn.onclick = (e) => {
        const li = e.target.closest('.deriv-step');
        li.classList.remove('locked');
        btn.remove();
      };
    });
  }
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function labelMap(k) {
  return ({
    villain_range: 'opponent',
    position: 'position',
    blockers: 'blockers',
    mdf: 'defense',
    range_type: 'range shape',
    equity_realization: 'equity realization',
    exploit: 'exploit',
    mixed_strategy: 'mixed strategy',
    spot_polarity: 'polarity',
    spot_exploit: 'exploit',
  })[k] || k.replace(/_/g, ' ');
}

function onCoachTipPolish(data) {
  state.lastPolish = data.text || '';
  // Polish supersedes the placeholder only when NOT in study mode (study mode's
  // pre-action prompt deliberately hides the recommendation).
  if (state.lastPolish && !state.studyMode) {
    $('coach-why').textContent = state.lastPolish;
    $('coach-why').dataset.placeholder = 'false';
  }
}

function onRatingUpdate(data) {
  if (data.overall_mu != null) {
    const mu = Math.round(data.overall_mu);
    $('skill-num').textContent = mu;
    const filled = Math.max(0, Math.min(1, (mu - 800) / 1600));
    const circumference = 2 * Math.PI * 26;
    $('skill-ring-fill').style.strokeDashoffset = String(circumference * (1 - filled));
    $('skill-label').textContent = skillLabel(mu);
  }
  const trend = $('skill-trend');
  if (data.delta_ev_bb != null) {
    if (state.currentTier <= 1) {
      const map = {
        great: '✓ great play',
        fine: '✓ fine',
        minor_leak: '~ small mistake',
        blunder: '✗ big mistake',
      };
      trend.textContent = map[data.bucket] || '';
    } else {
      const sign = data.delta_ev_bb >= 0 ? '+' : '';
      trend.textContent = `${sign}${data.delta_ev_bb.toFixed(1)}bb`;
    }
    trend.className = 'skill-trend ' + (data.delta_ev_bb >= -0.5 ? 'up' : 'down');
  }
  if (data.bucket) {
    state.bestPlayTotal++;
    if (data.bucket === 'great' || data.bucket === 'fine') state.bestPlayCount++;
    state.thisHandDecisions.push({
      bucket: data.bucket,
      ideal: data.ideal_action,
      delta: data.delta_ev_bb,
      userAction: data.user_action || null,
    });
    updateSessionStats();
    showPostActionFeedback(data);
  }
  if (data.streak) {
    updateStreakChip(data.streak);
  }
  state.lastRating = data;
}

function updateStreakChip(streak) {
  const chip = document.getElementById('streak-chip');
  document.getElementById('streak-current').textContent = streak.current;
  document.getElementById('streak-best').textContent = streak.longest;
  // Animate
  chip.classList.remove('bump', 'reset');
  // force reflow so animation re-triggers
  void chip.offsetWidth;
  if (streak.changed === 'incremented') {
    chip.classList.add('bump');
    setTimeout(() => chip.classList.remove('bump'), 350);
  } else if (streak.changed === 'reset') {
    chip.classList.add('reset');
    setTimeout(() => chip.classList.remove('reset'), 1200);
  }
}

function showPostActionFeedback(data) {
  // Post-action feedback panel — what the user just did vs the EV-max line.
  const el = $('post-action-feedback');
  if (!el) return;
  const bucket = data.bucket;
  const delta = data.delta_ev_bb || 0;
  const ideal = humanizeIdealAction(data.ideal_action);
  const userAction = humanizeIdealAction(data.user_action);

  let title, body, klass;
  if (bucket === 'great') {
    title = '✓ Great play';
    body = `You matched the EV-max line. ${ideal !== userAction ? `Best was ${ideal}.` : ''}`;
    klass = 'good';
  } else if (bucket === 'fine') {
    title = '✓ Fine play';
    body = `Close to optimal (within ${Math.abs(delta).toFixed(1)}bb of EV-max).`;
    klass = 'good';
  } else if (bucket === 'minor_leak') {
    title = `~ Small mistake — cost ${Math.abs(delta).toFixed(1)}bb`;
    body = `You picked <strong>${userAction}</strong>. The EV-max line was <strong>${ideal}</strong>.`;
    klass = 'warn';
  } else if (bucket === 'blunder') {
    title = `✗ Big mistake — cost ${Math.abs(delta).toFixed(1)}bb`;
    body = `You picked <strong>${userAction}</strong>. The EV-max line was <strong>${ideal}</strong> — try to remember this pattern.`;
    klass = 'bad';
  } else {
    return;
  }
  el.className = `post-action-feedback ${klass}`;
  el.innerHTML = `<div class="paf-title">${title}</div><div class="paf-body">${body}</div>`;
  el.hidden = false;
}

function humanizeIdealAction(key) {
  if (!key) return 'unknown';
  // The server sends internal keys (fold/check/call/raise_min/raise_big/bet_value)
  const labels = state.lastCoachMath && state.lastCoachMath.ev_labels || {};
  if (labels[key]) return labels[key];
  return ({
    fold: 'Fold',
    check: 'Check',
    call: 'Call',
    raise_min: 'Raise (min)',
    raise_big: 'Raise big',
    bet_value: 'Bet for value',
  })[key] || key;
}

function skillLabel(mu) {
  if (mu < 1200) return 'starting out';
  if (mu < 1500) return 'learning the ropes';
  if (mu < 1700) return 'solid player';
  if (mu < 1900) return 'strong player';
  if (mu < 2100) return 'tournament-level';
  return 'crushing it';
}

function onLeakReport(data) {
  const el = $('leak-block');
  if (!data || data.total === 0) {
    el.innerHTML = 'Play a few hands and we\'ll spot patterns.';
    return;
  }
  const blunderPct = (data.blunder_rate * 100).toFixed(0);
  const leakPct = (data.leak_rate * 100).toFixed(0);
  let html = `<div style="margin-bottom:6px"><strong>${data.total}</strong> recent decisions</div>`;
  html += `<div>${leakPct}% leaks · ${blunderPct}% blunders</div>`;
  if (data.top_leak) {
    html += `<div style="margin-top:10px;padding-top:10px;border-top:1px solid var(--border-subtle)">Most common pattern: <span class="leak-tag">${humanizeLeak(data.top_leak)}</span></div>`;
  }
  el.innerHTML = html;
}

function onDifficultyUpdate(data) {
  logEvent(`<em>Difficulty resampled — ${data.top_leak ? 'exploiting your ' + humanizeLeak(data.top_leak) : 'standard mix'}</em>`);
}

function onCoachAnswer(data) {
  appendChatMsg('coach', data.answer);
}

function onCoachNarration(data) {
  // Render a live coach insight in the hand stream. This is what teaches the
  // user to update their read TURN BY TURN as opponents act and the board changes.
  const ul = $('action-log');
  const li = document.createElement('li');
  li.className = 'narration';
  if (data.kind && data.kind.startsWith('board_')) {
    li.classList.add('narration-board');
  } else if (data.kind === 'preflop_3bet' || data.kind === 'preflop_4bet' || data.kind === 'raise') {
    li.classList.add('narration-aggression');
  }
  // Show headline + insight + range hint (collapsible by default — click to expand)
  let html = `<div class="narration-headline">💡 ${data.headline}</div>`;
  html += `<div class="narration-insight">${data.insight}</div>`;
  if (data.range_hint) {
    html += `<div class="narration-range">Likely range: <strong>${data.range_hint}</strong></div>`;
  }
  li.innerHTML = html;
  ul.insertBefore(li, ul.firstChild);
  while (ul.children.length > 80) ul.removeChild(ul.lastChild);
}

function onTierUpdate(data) {
  state.currentTier = data.tier;
  // Force re-render of coach panel if a tip is current
  if (state.lastCoachTip) {
    state.lastCoachTip.tier = data.tier;
    onCoachTip(state.lastCoachTip);
  }
}

function onHandEnd(data) {
  state.board = data.final_board;
  state.handComplete = true;
  $('action-bar').hidden = true;

  // Use the authoritative server-computed net for the hero.
  const heroNet = (typeof data.hero_net === 'number') ? data.hero_net : 0;
  if (heroNet > 0) state.handsWon++;
  state.netChips += heroNet;
  updateSessionStats();

  showRecap(data, heroNet);
  state.toActSeat = -1;
  render();
}

function onError(data) {
  logEvent(`<span style="color:var(--danger)">⚠ ${data.code}: ${data.message}</span>`);
}

// =============================================================
// Recap modal
// =============================================================
function showRecap(data, heroNet) {
  const hero = state.liveSeats[state.heroSeat];
  const overlay = $('recap-overlay');

  // Title — celebrate or commiserate based on NET, not gross
  if (heroNet > 0) {
    $('recap-title').innerHTML = `You netted <span style="color:var(--good)">+${heroNet}</span>`;
  } else if (heroNet < 0) {
    $('recap-title').innerHTML = `You lost <span style="color:var(--danger)">${heroNet}</span>`;
  } else {
    $('recap-title').textContent = 'Even hand — no chips changed';
  }

  // Subtitle — who took the pot
  const winnerLines = data.winners.map(w => {
    const isMe = w.player_id === hero.id;
    const name = isMe ? 'You' : findSeatNameById(w.player_id);
    return `<span class="recap-winner">${name}</span> took <span class="recap-amount">${w.amount}</span> with ${w.hand_desc}`;
  });
  $('recap-subtitle').innerHTML = winnerLines.join(' · ');

  // Board
  const board = $('recap-board');
  board.innerHTML = '';
  for (let i = 0; i < 5; i++) {
    const card = state.board[i] || data.final_board[i];
    if (card) {
      const suit = card[1];
      const color = (suit === 'h' || suit === 'd') ? 'red' : 'black';
      const rank = card[0] === 'T' ? '10' : card[0];
      const d = document.createElement('div');
      d.className = `recap-card ${color}`;
      d.innerHTML = `<div class="rank">${rank}</div><div class="suit">${suitSymbol(suit)}</div>`;
      board.appendChild(d);
    } else {
      const d = document.createElement('div');
      d.className = 'recap-card empty';
      board.appendChild(d);
    }
  }

  // Showdown
  const sdEl = $('recap-showdown');
  sdEl.innerHTML = '';
  if (data.showdown && data.showdown.length) {
    for (const s of data.showdown) {
      const isMe = s.player_id === hero.id;
      const isWinner = data.winners.some(w => w.player_id === s.player_id);
      const row = document.createElement('div');
      row.className = `showdown-row${isWinner ? ' winner' : ''}`;
      const name = isMe ? 'You' : findSeatNameById(s.player_id);
      row.innerHTML = `
        <span class="player">${name} ${s.cards.map(c => formatCardInline(c)).join(' ')}</span>
        <span class="hand">${s.hand_desc}</span>
      `;
      sdEl.appendChild(row);
    }
  }

  // Callouts — your-best-decision / worst-decision
  const callouts = $('recap-callouts');
  callouts.innerHTML = '';
  const decisions = state.thisHandDecisions;
  if (decisions.length) {
    const blunders = decisions.filter(d => d.bucket === 'blunder');
    const greats = decisions.filter(d => d.bucket === 'great');
    if (blunders.length) {
      const worst = blunders.reduce((a, b) => (a.delta < b.delta ? a : b));
      const div = document.createElement('div');
      div.className = 'callout bad';
      div.innerHTML = `<strong>Costly miss:</strong> you took the wrong line — ideal was <strong>${worst.ideal}</strong> (lost ~${Math.abs(worst.delta).toFixed(1)}bb of EV).`;
      callouts.appendChild(div);
    }
    if (greats.length && !blunders.length) {
      const div = document.createElement('div');
      div.className = 'callout good';
      div.innerHTML = `<strong>Well played</strong> — ${greats.length} of your ${decisions.length} decisions matched the EV-max line.`;
      callouts.appendChild(div);
    }
  }

  overlay.hidden = false;
}

function suitSymbol(s) {
  return { h: '♥', d: '♦', s: '♠', c: '♣' }[s] || s;
}
function formatCardInline(card) {
  const suit = card[1];
  const color = (suit === 'h' || suit === 'd') ? '#c62828' : '#1c1c1c';
  const rank = card[0] === 'T' ? '10' : card[0];
  return `<span style="background:#fafafa;color:${color};padding:1px 4px;border-radius:3px;font-weight:600;font-family:'JetBrains Mono',monospace;font-size:12px;margin:0 1px">${rank}${suitSymbol(suit)}</span>`;
}

function findSeatNameById(pid) {
  const idx = state.liveSeats.findIndex(s => s.id === pid);
  return idx >= 0 ? seatName(idx) : 'unknown';
}

function updateSessionStats() {
  const el = $('stat-won');
  el.textContent = `${state.handsWon}/${state.handCounter}`;
  const net = $('stat-net');
  net.textContent = state.netChips > 0 ? `+${state.netChips}` : String(state.netChips);
  net.className = 'value ' + (state.netChips > 0 ? 'up' : state.netChips < 0 ? 'down' : '');
  if (state.bestPlayTotal > 0) {
    const pct = Math.round(100 * state.bestPlayCount / state.bestPlayTotal);
    $('stat-best-play').textContent = `${pct}%`;
  }
}

// =============================================================
// Helpers
// =============================================================
function seatName(seat) {
  const s = state.liveSeats[seat] || state.seats[seat];
  if (!s) return `seat ${seat}`;
  if (state.currentTier <= 1) {
    return s.name.replace(/\s*\([^)]+\)\s*$/, '');
  }
  return s.name;
}

function humanizeSpot(spot) {
  const map = {
    'value_raise': '💰 Value raise',
    'value_bet':   '💰 Value bet',
    'value_call':  '✓ Value call',
    'bluff_catch': '🎯 Bluff catcher',
    'semi_bluff':  '🌀 Semi-bluff',
    'pure_bluff':  '🃏 Pure bluff',
    'marginal':    '⚖ Marginal',
    'give_up':     '✋ Check / fold',
  };
  return map[spot] || spot;
}

function humanizeArchetype(name) {
  const map = {
    'TAG': 'tight, aggressive',
    'LAG': 'loose, aggressive',
    'Nit': 'very tight',
    'Calling Station': 'calls a lot',
    'Maniac': 'hyper-aggressive',
    'Whale': 'loose, passive',
    'GTO Reg': 'balanced expert',
  };
  if (state.currentTier <= 1) return map[name] || name;
  return name;
}

function humanizePosition(pos) {
  const map = {
    'BTN': 'Dealer',
    'SB': 'Small Blind',
    'BB': 'Big Blind',
    'UTG': 'Early',
    'UTG+1': 'Early',
    'MP': 'Middle',
    'LJ': 'Middle',
    'HJ': 'Late',
    'CO': 'Late',
  };
  return map[pos] || pos;
}

function humanizeLeak(tag) {
  const map = {
    fold_too_much: 'folding too often when ahead',
    fold_to_aggression: 'folding to aggression',
    call_too_much: 'calling too loose',
    fail_to_value_raise: 'missing value raises',
    bluff_too_much: 'over-bluffing',
    over_aggression: 'over-aggression',
    under_aggression: 'under-aggression',
    misc: 'miscellaneous misplay',
  };
  return map[tag] || tag;
}

function logEvent(html, cssClass = null) {
  const ul = $('action-log');
  const li = document.createElement('li');
  li.innerHTML = html;
  if (cssClass) li.classList.add('action-' + cssClass);
  ul.insertBefore(li, ul.firstChild);
  while (ul.children.length > 80) ul.removeChild(ul.lastChild);
}

function clearLog() { $('action-log').innerHTML = ''; }

// =============================================================
// SVG Table render
// =============================================================
const T_W = 1000, T_H = 640, T_CX = 500, T_CY = 340;
const T_RX = 400, T_RY = 220;

function seatPosition(seat, n) {
  const heroOffset = state.heroSeat || 0;
  const i = (seat - heroOffset + n) % n;
  const angle = Math.PI / 2 + (i * 2 * Math.PI / n);
  const factor = 1.04;
  const x = T_CX + T_RX * factor * Math.cos(angle);
  const y = T_CY + T_RY * factor * Math.sin(angle);
  return { x, y, angle };
}

function render() {
  const svg = $('table');
  svg.innerHTML = '';
  svg.appendChild(sk.defs());

  // Felt (outer rim → gold ring → inner felt)
  svg.appendChild(sk.svgEl('ellipse', {
    cx: T_CX, cy: T_CY + 8, rx: T_RX + 18, ry: T_RY + 18, class: 'felt-rim-shadow',
  }));
  svg.appendChild(sk.svgEl('ellipse', {
    cx: T_CX, cy: T_CY, rx: T_RX + 18, ry: T_RY + 18, class: 'felt-outer',
  }));
  svg.appendChild(sk.svgEl('ellipse', {
    cx: T_CX, cy: T_CY, rx: T_RX + 6, ry: T_RY + 6, class: 'felt-rim',
  }));
  svg.appendChild(sk.svgEl('ellipse', {
    cx: T_CX, cy: T_CY, rx: T_RX, ry: T_RY, class: 'felt-inner',
  }));
  // Decorative inner ring
  svg.appendChild(sk.svgEl('ellipse', {
    cx: T_CX, cy: T_CY, rx: T_RX - 30, ry: T_RY - 30, class: 'felt-pattern',
  }));

  // Pot pill (centered, above board cards)
  const potY = T_CY - 90;
  svg.appendChild(sk.svgEl('rect', {
    x: T_CX - 80, y: potY - 22, width: 160, height: 44, rx: 22, ry: 22, class: 'pot-pill',
  }));
  svg.appendChild(sk.svgEl('text', { x: T_CX, y: potY - 6, class: 'pot-label' }, 'POT'));
  svg.appendChild(sk.svgEl('text', { x: T_CX, y: potY + 14, class: 'pot-amount' }, String(state.pot)));

  // Board cards (5 slots, centered)
  const totalW = 5 * sk.W + 4 * 8;
  let bx = T_CX - totalW / 2;
  const by = T_CY - 25;
  for (let i = 0; i < 5; i++) {
    if (state.board[i]) {
      svg.appendChild(sk.cardFront(bx, by, state.board[i]));
    } else {
      svg.appendChild(sk.cardSlot(bx, by));
    }
    bx += sk.W + 8;
  }

  // Street label below board
  svg.appendChild(sk.svgEl('text', {
    x: T_CX, y: T_CY + sk.H + 5, class: 'street-label',
  }, streetLabel(state.street)));

  // Seats
  const n = state.liveSeats.length;
  for (let i = 0; i < n; i++) {
    drawSeat(svg, i, n);
  }
}

function streetLabel(s) {
  if (state.currentTier <= 1) {
    return { preflop: 'before the cards', flop: 'first 3 cards', turn: 'fourth card', river: 'final card' }[s] || s;
  }
  return s;
}

function drawSeat(svg, seat, n) {
  const p = seatPosition(seat, n);
  const info = state.liveSeats[seat];
  const isHero = seat === state.heroSeat;
  const isActive = seat === state.toActSeat;

  const w = 150, h = 64;
  const x = p.x - w / 2;
  const y = p.y - h / 2;

  // Active glow halo
  if (isActive) {
    svg.appendChild(sk.svgEl('rect', {
      x: x - 4, y: y - 4, width: w + 8, height: h + 8, rx: 12, ry: 12,
      fill: 'none', stroke: 'var(--accent)', 'stroke-width': 2,
      style: 'filter: drop-shadow(0 0 12px rgba(110,168,255,0.6));',
    }));
  }

  // Tile
  let bgClass = 'seat-bg';
  if (isActive) bgClass += ' active';
  if (info.folded) bgClass += ' folded';
  if (isHero) bgClass += ' hero';
  svg.appendChild(sk.svgEl('rect', {
    x, y, width: w, height: h, rx: 10, ry: 10, class: bgClass,
  }));

  // Name + style line
  const displayName = info.name.replace(/\s*\(.*\)$/, '');
  svg.appendChild(sk.svgEl('text', { x: p.x, y: y + 18, class: 'seat-name' }, displayName));
  if (!isHero && info.archetype) {
    const archDisplay = humanizeArchetype(info.archetype);
    svg.appendChild(sk.svgEl('text', { x: p.x, y: y + 32, class: 'seat-arch' }, archDisplay));
  }

  // Stack (chip count)
  svg.appendChild(sk.svgEl('text', { x: p.x, y: y + (isHero ? 38 : 48), class: 'seat-stack' }, String(info.stack)));

  // Position pill
  if (info.position) {
    const posDisplay = state.currentTier <= 1 ? humanizePosition(info.position) : info.position;
    svg.appendChild(sk.svgEl('text', { x: p.x, y: y + h - 6, class: 'seat-pos' }, posDisplay));
  }

  // Last action ghost (shows what they just did)
  if (info.lastAction && !isActive && !info.folded) {
    svg.appendChild(sk.svgEl('text', {
      x: p.x, y: y + h + 14, class: 'last-action-label',
    }, info.lastAction));
  }

  // Hole cards (above the seat tile)
  const cardY = y - sk.H - 4;
  if (info.folded) {
    // skip
  } else if (isHero && state.heroCards.length === 2) {
    svg.appendChild(sk.cardFront(p.x - sk.W - 3, cardY, state.heroCards[0]));
    svg.appendChild(sk.cardFront(p.x + 3, cardY, state.heroCards[1]));
  } else {
    svg.appendChild(sk.cardBack(p.x - sk.W - 3, cardY));
    svg.appendChild(sk.cardBack(p.x + 3, cardY));
  }

  // Dealer button — on the inner side
  if (seat === state.buttonSeat) {
    const inner = innerDir(p);
    const bx = p.x + inner.dx * 38;
    const by = p.y + inner.dy * 36 - 18;
    svg.appendChild(sk.svgEl('circle', { cx: bx, cy: by, r: 12, class: 'dealer-button' }));
    svg.appendChild(sk.svgEl('circle', { cx: bx, cy: by, r: 9, class: 'dealer-button-ring' }));
    svg.appendChild(sk.svgEl('text', { x: bx, y: by + 4, class: 'dealer-button-text' }, 'D'));
  }

  // Bet — chip + amount in the inner direction
  if (info.bet > 0) {
    const inner = innerDir(p);
    const bx = p.x + inner.dx * 85;
    const by = p.y + inner.dy * 65;
    svg.appendChild(sk.svgEl('circle', { cx: bx, cy: by, r: 14, class: 'bet-chip' }));
    svg.appendChild(sk.svgEl('circle', { cx: bx, cy: by, r: 10, class: 'bet-chip-inner' }));
    svg.appendChild(sk.svgEl('text', { x: bx, y: by + 4, class: 'bet-amount' }, String(info.bet)));
  }
}

function innerDir(p) {
  const dx = T_CX - p.x;
  const dy = T_CY - p.y;
  const len = Math.hypot(dx, dy) || 1;
  return { dx: dx / len, dy: dy / len };
}

// =============================================================
// Action bar
// =============================================================
function showActionBar(req) {
  $('action-bar').hidden = false;
  $('pot').textContent = req.pot;
  $('to-call').textContent = req.to_call > 0 ? req.to_call : '—';
  const hero = state.liveSeats[state.heroSeat];
  $('hero-stack').textContent = hero ? hero.stack : '—';

  const legal = req.legal;
  // HIDE rather than disable buttons that are illegal — disabled buttons
  // confuse novices ("I want to fold but the button is grey"). Only show
  // what the user can actually do right now.
  $('btn-fold').hidden = !legal.can_fold;
  $('btn-check').hidden = !legal.can_check;
  $('btn-call').hidden = !legal.can_call;
  $('btn-bet').hidden = !legal.can_bet;
  $('btn-raise').hidden = !legal.can_raise;
  $('btn-allin').hidden = legal.max_raise_to <= 0;

  // Belt-and-suspenders: also leave them not-disabled if shown
  $('btn-fold').disabled = false;
  $('btn-check').disabled = false;
  $('btn-call').disabled = false;
  $('btn-bet').disabled = false;
  $('btn-raise').disabled = false;
  $('btn-allin').disabled = false;

  // Per-button amount labels
  $('btn-call-amt').textContent = legal.can_call ? String(legal.call_amount) : '';
  $('btn-bet-amt').textContent = legal.can_bet ? `~${Math.max(Math.floor(req.pot * 0.66), state.bb)}` : '';
  $('btn-raise-amt').textContent = legal.can_raise ? `to ${legal.min_raise_to}` : '';
  $('btn-allin-amt').textContent = legal.max_raise_to > 0 ? String(legal.max_raise_to) : '';

  // Bet slider hidden by default; shown only when Bet or Raise tapped
  $('bet-row').hidden = true;
  $('bet-slider').min = legal.min_raise_to || 0;
  $('bet-slider').max = legal.max_raise_to || 0;
  $('bet-slider').value = legal.min_raise_to || 0;
  $('bet-amount').min = legal.min_raise_to || 0;
  $('bet-amount').max = legal.max_raise_to || 0;
  $('bet-amount').value = legal.min_raise_to || 0;

  highlightRecommendedButton();
}

function highlightRecommendedButton() {
  ['fold', 'check', 'call', 'bet', 'raise', 'allin'].forEach((id) => {
    const el = $(`btn-${id}`);
    if (el) el.classList.remove('recommended');
  });
  if (state.studyMode) return;
  const rec = state.recommendedButton;
  if (!rec) return;
  const id = rec === 'all_in' ? 'btn-allin' : `btn-${rec}`;
  const el = $(id);
  if (el && !el.hidden && !el.disabled) el.classList.add('recommended');
}

function sendAction(actionKey, amount = 0) {
  if (!state.toActReq) return;
  send('action', {
    hand_id: state.toActReq.hand_id,
    seq: state.toActReq.seq,
    action: actionKey,
    amount,
  });
  state.awaitingUser = false;
  $('action-bar').hidden = true;
  $('coach-empty').hidden = false;
  $('coach-content').hidden = true;
}

// =============================================================
// Wiring
// =============================================================
function wireControls() {
  // Action buttons — close bet-row whenever a non-sizing action is taken
  const closeBetRow = () => { $('bet-row').hidden = true; };
  $('btn-fold').onclick = () => { closeBetRow(); sendAction('fold'); };
  $('btn-check').onclick = () => { closeBetRow(); sendAction('check'); };
  $('btn-call').onclick = () => { closeBetRow(); sendAction('call'); };
  $('btn-bet').onclick = () => {
    if (state.recommendedButton === 'bet') {
      const target = parseInt($('btn-bet-amt').textContent.replace(/[^\d]/g, ''), 10) || state.bb;
      sendAction('bet', target);
    } else {
      $('bet-row').hidden = false;
      $('bet-confirm').dataset.kind = 'bet';
    }
  };
  $('btn-raise').onclick = () => {
    if (state.recommendedButton === 'raise') {
      sendAction('raise', parseInt(state.toActReq.legal.min_raise_to, 10));
    } else {
      $('bet-row').hidden = false;
      $('bet-confirm').dataset.kind = 'raise';
    }
  };
  $('btn-allin').onclick = () => { closeBetRow(); sendAction('all_in', parseInt($('btn-allin-amt').textContent, 10) || 0); };
  $('bet-confirm').onclick = () => {
    const amt = parseInt($('bet-amount').value, 10);
    const kind = $('bet-confirm').dataset.kind || 'bet';
    closeBetRow();
    sendAction(kind, amt);
  };
  $('bet-cancel').onclick = closeBetRow;

  // Keyboard shortcuts — F/K/C/R/B/A and Esc to cancel sizing
  document.addEventListener('keydown', (e) => {
    // Don't fire shortcuts while typing in inputs
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
    if (!state.awaitingUser) return;
    const key = e.key.toLowerCase();
    const map = {
      f: 'btn-fold',
      k: 'btn-check',
      c: 'btn-call',
      b: 'btn-bet',
      r: 'btn-raise',
      a: 'btn-allin',
    };
    if (map[key]) {
      const btn = $(map[key]);
      if (btn && !btn.hidden && !btn.disabled) {
        e.preventDefault();
        btn.click();
      }
    } else if (e.key === 'Escape') {
      closeBetRow();
    } else if (e.key === 'Enter' && !$('bet-row').hidden) {
      e.preventDefault();
      $('bet-confirm').click();
    }
  });

  // Slider syncs
  $('bet-slider').addEventListener('input', (e) => { $('bet-amount').value = e.target.value; });
  $('bet-amount').addEventListener('input', (e) => { $('bet-slider').value = e.target.value; });
  document.querySelectorAll('.quick-sizes button').forEach((b) => {
    b.onclick = () => {
      if (!state.toActReq) return;
      const frac = parseFloat(b.dataset.frac);
      const pot = state.toActReq.pot;
      const target = Math.max(state.toActReq.legal.min_raise_to, Math.floor(pot * frac));
      const clamped = Math.min(target, state.toActReq.legal.max_raise_to);
      $('bet-slider').value = clamped;
      $('bet-amount').value = clamped;
    };
  });

  // Recap → next hand
  $('btn-next-hand').onclick = () => {
    send('next_hand', {});
    $('recap-overlay').hidden = true;
  };

  // Show math toggle
  $('show-math-btn').onclick = () => {
    const d = $('math-drawer');
    const open = !d.hidden;
    d.hidden = open;
    $('show-math-btn').textContent = open ? 'Show math ▾' : 'Hide math ▴';
  };

  // Tier selector
  $('tier-select').addEventListener('change', (e) => {
    const v = e.target.value;
    send('set_tier', { tier: v === 'auto' ? null : parseInt(v, 10) });
  });

  // Study mode toggle — hides recommendation pre-action
  $('study-mode-toggle').addEventListener('change', (e) => {
    state.studyMode = e.target.checked;
    // If a tip is currently visible, re-render it under the new mode
    if (state.lastCoachTip) onCoachTip(state.lastCoachTip);
  });

  // Tabs
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.onclick = () => switchTab(tab.dataset.tab);
  });

  // Drill modal close
  $('drill-close').onclick = () => {
    $('drill-modal').hidden = true;
    currentDrill = null;
  };

  // Quiz modal
  $('quiz-submit').onclick = submitQuizAnswers;
  $('quiz-close').onclick = () => {
    // Treat close-without-submit as all-revealed (counts as wrong for streak)
    if (!state.pendingQuiz) {
      $('quiz-overlay').hidden = true;
      return;
    }
    const answers = (state.pendingQuiz.questions || []).map(() => '__revealed__');
    send('submit_quiz_answer', { answers, revealed: true });
  };

  // Stage-up modal
  $('stage-up-stay').onclick = () => { $('stage-up-overlay').hidden = true; };
  $('stage-up-go').onclick = () => {
    $('stage-up-overlay').hidden = true;
    send('graduate_stage', {});
  };

  // Walkthrough overlay
  $('walkthrough-back').onclick = () => {
    if (_walkthroughIdx > 0) {
      _walkthroughIdx -= 1;
      renderWalkthroughStep();
    }
  };
  $('walkthrough-next').onclick = () => {
    if (_walkthroughIdx >= _walkthroughSteps.length - 1) {
      closeWalkthrough();
    } else {
      _walkthroughIdx += 1;
      renderWalkthroughStep();
    }
  };
  $('walkthrough-skip').onclick = () => {
    closeWalkthrough();
  };

  // Onboarding tour
  $('tour-skip').onclick = () => {
    $('tour-overlay').hidden = true;
    localStorage.setItem('thefelt_tour_done', '1');
    state._customTour = null;
  };
  $('tour-next').onclick = () => {
    tourIdx += 1;
    renderTourStep();
  };

  // Q&A
  const askCoach = (text) => {
    const q = text || $('qa-input').value.trim();
    if (!q) return;
    appendChatMsg('user', q);
    appendChatMsg('coach', 'thinking…', { loading: true });
    send('ask_coach', { question: q });
    $('qa-input').value = '';
  };
  $('qa-send').onclick = () => askCoach();
  $('qa-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') askCoach(); });
  document.querySelectorAll('.qa-suggest').forEach((b) => {
    b.onclick = () => askCoach(b.dataset.q);
  });
}

function appendChatMsg(who, text, opts = {}) {
  const log = $('qa-log');
  // Remove any loading message
  log.querySelectorAll('.chat-msg.loading').forEach(el => el.remove());
  const div = document.createElement('div');
  div.className = `chat-msg ${who}${opts.loading ? ' loading' : ''}`;
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

// =============================================================
// Curriculum loading & rendering
// =============================================================
async function loadCurriculum() {
  try {
    const catalog = await fetch('/api/lessons').then(r => r.json());
    curriculum.modules = catalog.modules || [];
  } catch (e) {
    console.error('Failed to load lesson catalog:', e);
    return;
  }
  // Progress is loaded after we know our user_id (from `joined`)
  renderLessonList();
}

async function loadProgress() {
  if (!state.userId) return;
  try {
    const data = await fetch(`/api/users/${state.userId}/progress`).then(r => r.json());
    curriculum.progress = data.progress || {};
  } catch (e) {
    console.error('Failed to load progress:', e);
  }
  renderLessonList();
}

function renderLessonList() {
  const root = $('drills-list');
  root.innerHTML = '';
  if (!curriculum.modules.length) {
    root.innerHTML = '<div class="muted" style="padding:8px">No lessons loaded.</div>';
    return;
  }
  for (const m of curriculum.modules) {
    const moduleHeader = document.createElement('div');
    moduleHeader.className = 'drill-module';
    moduleHeader.textContent = `${m.id} · ${m.title}`;
    root.appendChild(moduleHeader);
    for (const l of m.lessons) {
      const p = curriculum.progress[l.id] || { state: 'active', attempts: 0, correct: 0, recent_accuracy: 0 };
      const item = document.createElement('div');
      item.className = `drill-lesson ${p.state}`;
      const stateSymbol = p.state === 'mastered' ? '✓' : p.state === 'active' ? '·' : '🔒';
      item.innerHTML = `
        <div class="drill-state">${stateSymbol}</div>
        <div class="drill-info">
          <div class="drill-title-row">
            <span class="drill-name">${l.title}</span>
            <span class="drill-progress">${p.correct}/${p.attempts}</span>
          </div>
          <div class="drill-bar"><div class="fill" style="width:${Math.min(100, p.recent_accuracy * 100)}%"></div></div>
        </div>
      `;
      if (p.state !== 'locked') {
        item.onclick = () => startDrill(l);
      }
      root.appendChild(item);
    }
  }
  // Render recommendation
  renderRecommendation();
}

function renderRecommendation() {
  const rec = $('drills-recommendation');
  // Find the first non-mastered lesson (simple recommendation; server-side leak-based logic kicks in too)
  let recommended = null;
  for (const m of curriculum.modules) {
    for (const l of m.lessons) {
      const p = curriculum.progress[l.id];
      if (!p || p.state !== 'mastered') {
        recommended = { lesson: l, module: m };
        break;
      }
    }
    if (recommended) break;
  }
  if (!recommended) {
    rec.hidden = true;
    return;
  }
  rec.hidden = false;
  const leakNote = state.lastRating?.leak_tag
    ? `Your recent play shows <strong>${humanizeLeak(state.lastRating.leak_tag)}</strong>. `
    : '';
  rec.innerHTML = `
    <div>${leakNote}Pick up where you left off:</div>
    <div style="margin-top:4px"><strong>${recommended.module.id} · ${recommended.lesson.title}</strong></div>
    <button class="start-rec">Start drill →</button>
  `;
  rec.querySelector('.start-rec').onclick = () => startDrill(recommended.lesson);
}

let currentDrill = null;

function startDrill(lesson) {
  currentDrill = { lesson_id: lesson.id, lesson_title: lesson.title };
  curriculum.activeLesson = lesson.id;
  send('set_active_lesson', { lesson_id: lesson.id });
  send('start_drill', { lesson_id: lesson.id, drill_kind: lesson.drill_kind });
  $('drill-modal').hidden = false;
  $('drill-lesson-title').textContent = lesson.title;
  $('drill-question').textContent = 'Loading…';
  $('drill-context').textContent = '';
  $('drill-choices').innerHTML = '';
  $('drill-numeric').hidden = true;
  $('drill-submit').hidden = true;
  $('drill-feedback').hidden = true;
  $('drill-next').hidden = true;
  // Make sure user is on Drills tab
  switchTab('drills');
}

function onDrillQuestion(data) {
  $('drill-question').textContent = data.question;
  // Render context (e.g. card layout) if present
  const ctxEl = $('drill-context');
  if (data.context && (data.context.hole || data.context.board)) {
    const hole = (data.context.hole || []).join(' ');
    const board = (data.context.board || []).join(' ');
    ctxEl.textContent = `Your cards: ${hole}   Board: ${board || '—'}`;
  } else {
    ctxEl.textContent = '';
  }

  const choicesEl = $('drill-choices');
  const numericEl = $('drill-numeric');
  const submitEl = $('drill-submit');
  choicesEl.innerHTML = '';
  if (data.answer_type === 'mc') {
    numericEl.hidden = true;
    submitEl.hidden = true;
    (data.choices || []).forEach((choice, i) => {
      const btn = document.createElement('button');
      btn.textContent = choice;
      btn.onclick = () => {
        // Disable all buttons & submit
        choicesEl.querySelectorAll('button').forEach(b => b.disabled = true);
        send('submit_drill_answer', { answer: i });
      };
      choicesEl.appendChild(btn);
    });
  } else if (data.answer_type === 'numeric') {
    numericEl.hidden = false;
    numericEl.value = '';
    numericEl.focus();
    submitEl.hidden = false;
    submitEl.onclick = () => {
      submitEl.disabled = true;
      send('submit_drill_answer', { answer: parseFloat(numericEl.value) });
    };
  }
  $('drill-feedback').hidden = true;
  $('drill-next').hidden = true;
}

function onDrillFeedback(data) {
  const fb = $('drill-feedback');
  fb.hidden = false;
  fb.className = `drill-feedback ${data.correct ? 'correct' : 'incorrect'}`;
  fb.innerHTML = `
    <strong>${data.correct ? '✓ Correct.' : '✗ Not quite.'}</strong>
    <span class="correct-answer">Answer: ${data.correct_answer}</span>
    <div style="margin-top:6px">${data.explanation}</div>
  `;
  // Highlight the right MC choice
  $('drill-choices').querySelectorAll('button').forEach((b, i) => {
    if (b.textContent === data.correct_answer) b.classList.add('correct');
    else if (b.disabled) b.classList.add('incorrect');
  });
  $('drill-submit').hidden = true;
  $('drill-numeric').hidden = (data.kind ? false : true);
  if (!currentDrill) return;
  // Show next button
  const nxt = $('drill-next');
  nxt.hidden = false;
  nxt.onclick = () => {
    const lesson = curriculum.modules.flatMap(m => m.lessons).find(l => l.id === currentDrill.lesson_id);
    if (lesson) startDrill(lesson);
  };
  // Refresh progress (server has just persisted the attempt)
  loadProgress();
}

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.toggle('active', p.dataset.pane === name));
}

// Refresh progress after every rating_update (live decisions credit toward modules)
const _originalOnRatingUpdate = onRatingUpdate;
window.onRatingUpdate = onRatingUpdate;  // expose for re-wiring


// =============================================================
// Pilot-style training: stage HUD + quiz modal
// =============================================================
function updateStageHUD() {
  document.getElementById('stage-id').textContent = state.stageId;
  document.getElementById('stage-title').textContent = state.stageTitle;
  // Light up axes 1..stageId-1 as "unlocked" (mastered), stageId as "active",
  // rest as locked.
  document.querySelectorAll('.axis').forEach((el) => {
    const a = parseInt(el.dataset.axis, 10);
    el.classList.remove('unlocked', 'active');
    if (a < state.stageId) el.classList.add('unlocked');
    else if (a === state.stageId) el.classList.add('active');
  });
}

function onStageQuiz(data) {
  state.pendingQuiz = data;
  state.quizAnswers = {};
  state.stageId = data.stage_id;
  state.stageTitle = data.stage_title;
  state.streakInStage = data.correct_streak || 0;
  updateStageHUD();
  // Show the walkthrough FIRST if the student hasn't seen this stage yet
  if (!hasSeenWalkthrough(data.stage_id)) {
    showWalkthroughForStage(data.stage_id, () => {
      renderQuizModal();
    });
    return;
  }
  renderQuizModal();
}

// =============================================================
// Walkthrough overlay — pre-stage lesson
// =============================================================
function hasSeenWalkthrough(stageId) {
  const seen = JSON.parse(localStorage.getItem('thefelt_walkthroughs_seen') || '[]');
  return seen.includes(stageId);
}

function markWalkthroughSeen(stageId) {
  const seen = JSON.parse(localStorage.getItem('thefelt_walkthroughs_seen') || '[]');
  if (!seen.includes(stageId)) {
    seen.push(stageId);
    localStorage.setItem('thefelt_walkthroughs_seen', JSON.stringify(seen));
  }
}

let _walkthroughSteps = [];
let _walkthroughIdx = 0;
let _walkthroughOnDone = null;
let _walkthroughStageId = 1;

async function showWalkthroughForStage(stageId, onDone) {
  try {
    if (!curriculum.stages) {
      const data = await fetch('/api/stages').then(r => r.json());
      curriculum.stages = data.stages || [];
    }
  } catch (e) {
    console.error('Failed to load stages', e);
    onDone && onDone();
    return;
  }
  const stage = (curriculum.stages || []).find(s => s.id === stageId);
  if (!stage || !stage.walkthrough || !stage.walkthrough.length) {
    markWalkthroughSeen(stageId);
    onDone && onDone();
    return;
  }
  _walkthroughSteps = stage.walkthrough;
  _walkthroughIdx = 0;
  _walkthroughOnDone = onDone;
  _walkthroughStageId = stageId;
  document.getElementById('walkthrough-stage').textContent = `Stage ${stage.id} · ${stage.title}`;
  document.getElementById('walkthrough-overlay').hidden = false;
  renderWalkthroughStep();
}

function renderWalkthroughStep() {
  const step = _walkthroughSteps[_walkthroughIdx];
  if (!step) {
    closeWalkthrough();
    return;
  }
  document.getElementById('walkthrough-progress').textContent =
    `Step ${_walkthroughIdx + 1} of ${_walkthroughSteps.length}`;

  const body = document.getElementById('walkthrough-body');
  let html = `<div class="walkthrough-title">${escapeHtml(step.title)}</div>`;
  html += `<div class="walkthrough-text">${step.body}</div>`;
  if (step.formula) {
    html += `<div class="walkthrough-formula">${escapeHtml(step.formula)}</div>`;
  }
  if ((step.example_hole && step.example_hole.length) || (step.example_board && step.example_board.length)) {
    html += `<div class="walkthrough-example">
      <div class="walkthrough-example-label">Worked example</div>
      <div class="walkthrough-example-cards">`;
    if (step.example_hole && step.example_hole.length) {
      html += `<div class="quiz-cards-group">
        <div class="quiz-cards-label">your hand</div>
        <div class="quiz-cards-row">${step.example_hole.map(wtCardHtml).join('')}</div>
      </div>`;
    }
    if (step.example_board && step.example_board.length) {
      html += `<div class="quiz-cards-group">
        <div class="quiz-cards-label">board</div>
        <div class="quiz-cards-row">${step.example_board.map(wtCardHtml).join('')}</div>
      </div>`;
    }
    html += `</div>`;
    if (step.example_answer) {
      html += `<div class="walkthrough-example-answer">→ ${escapeHtml(step.example_answer)}</div>`;
    }
    html += `</div>`;
  }
  body.innerHTML = html;

  document.getElementById('walkthrough-back').hidden = _walkthroughIdx === 0;
  const isLast = _walkthroughIdx === _walkthroughSteps.length - 1;
  document.getElementById('walkthrough-next').textContent = isLast ? 'Got it — start the quiz' : 'Next →';
}

function wtCardHtml(card) {
  const rank = card[0] === 'T' ? '10' : card[0];
  const suit = card[1];
  const symbol = { h: '♥', d: '♦', s: '♠', c: '♣' }[suit] || suit;
  const color = (suit === 'h' || suit === 'd') ? 'red' : 'black';
  return `<div class="quiz-card ${color}"><div class="rank">${rank}</div><div class="suit">${symbol}</div></div>`;
}

function closeWalkthrough() {
  document.getElementById('walkthrough-overlay').hidden = true;
  markWalkthroughSeen(_walkthroughStageId);
  if (_walkthroughOnDone) {
    const fn = _walkthroughOnDone;
    _walkthroughOnDone = null;
    fn();
  }
}

function renderQuizModal() {
  const data = state.pendingQuiz;
  if (!data) return;
  document.getElementById('quiz-stage-id').textContent = data.stage_id;
  document.getElementById('quiz-stage-title').textContent = data.stage_title;
  document.getElementById('quiz-stage-teaches').textContent = data.stage_teaches;
  renderQuizSituation(data.situation);
  document.getElementById('quiz-streak-num').textContent = data.correct_streak || 0;
  const freqLabels = {
    1.0: 'every turn',
    0.66: 'every 2-3 turns',
    0.33: 'every 3-4 turns',
    0.20: 'occasionally',
  };
  const freqText = freqLabels[Math.round(data.frequency * 100) / 100] || `${Math.round((data.frequency||1)*100)}% of turns`;
  document.getElementById('quiz-freq-mini').textContent = `quiz: ${freqText}`;

  const wrap = document.getElementById('quiz-questions');
  wrap.innerHTML = '';
  (data.questions || []).forEach((q, idx) => {
    const qDiv = document.createElement('div');
    qDiv.className = 'quiz-q';
    qDiv.dataset.qid = q.id;
    qDiv.innerHTML = `
      <div class="quiz-q-prompt"><span class="q-num">${idx + 1}</span>${escapeHtml(q.prompt)}</div>
      <div class="quiz-q-input"></div>
      <div class="quiz-q-help">
        <button class="qh-hint">Hint</button>
        <button class="qh-show">Show me</button>
      </div>
    `;
    const inputWrap = qDiv.querySelector('.quiz-q-input');
    if (q.answer_type === 'numeric') {
      const input = document.createElement('input');
      input.type = 'number';
      input.step = '0.1';
      input.placeholder = '?';
      input.dataset.qid = q.id;
      input.addEventListener('input', () => {
        state.quizAnswers[q.id] = input.value === '' ? null : parseFloat(input.value);
        updateQuizSubmitState();
      });
      inputWrap.appendChild(input);
      // Add a "% / chips" suffix label depending on question
      const suffix = document.createElement('span');
      suffix.className = 'q-suffix';
      if (q.id === 'pot_odds') suffix.textContent = '% needed';
      else if (q.id === 'ev_call') suffix.textContent = 'chips of EV';
      else if (q.id === 'outs') suffix.textContent = 'outs';
      inputWrap.appendChild(suffix);
    } else if (q.answer_type === 'mc') {
      const list = document.createElement('div');
      list.className = 'quiz-q-choices';
      (q.choices || []).forEach((choice, i) => {
        const b = document.createElement('button');
        b.textContent = choice;
        b.onclick = () => {
          list.querySelectorAll('button').forEach(x => x.classList.remove('selected'));
          b.classList.add('selected');
          // For hand_class the correct answer is a STRING (server compares text);
          // for the rest, the correct is an INDEX.
          state.quizAnswers[q.id] = q.id === 'hand_class' ? choice : i;
          updateQuizSubmitState();
        };
        list.appendChild(b);
      });
      inputWrap.appendChild(list);
    }
    // Hint / show-me buttons
    qDiv.querySelector('.qh-hint').onclick = () => {
      const help = document.createElement('div');
      help.className = 'quiz-q-revealed';
      help.textContent = '💡 ' + (q.hint || 'No hint available.');
      qDiv.appendChild(help);
    };
    qDiv.querySelector('.qh-show').onclick = () => {
      const help = document.createElement('div');
      help.className = 'quiz-q-revealed';
      help.textContent = '📖 ' + (q.formula || q.hint || '');
      qDiv.appendChild(help);
      state.quizAnswers[q.id] = '__revealed__';
      updateQuizSubmitState();
    };
    wrap.appendChild(qDiv);
  });

  // Render "what the app handles for you" preview
  const handled = $('quiz-handled');
  const handledData = data.handled_summary || {};
  if (Object.keys(handledData).length > 0) {
    let html = '<div class="quiz-handled-title">We\'re handling these for you</div><dl class="quiz-handled-grid">';
    for (const [k, v] of Object.entries(handledData)) {
      html += `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`;
    }
    html += '</dl>';
    handled.innerHTML = html;
    handled.hidden = false;
  } else {
    handled.hidden = true;
  }

  document.getElementById('quiz-overlay').hidden = false;
  updateQuizSubmitState();
}

function renderQuizSituation(sit) {
  const el = document.getElementById('quiz-situation');
  if (!sit) {
    el.innerHTML = '';
    el.style.display = 'none';
    return;
  }
  el.style.display = 'grid';

  const cardHtml = (card) => {
    if (!card) {
      return '<div class="quiz-card empty"></div>';
    }
    const rank = card[0] === 'T' ? '10' : card[0];
    const suit = card[1];
    const symbol = { h: '♥', d: '♦', s: '♠', c: '♣' }[suit] || suit;
    const color = (suit === 'h' || suit === 'd') ? 'red' : 'black';
    return `<div class="quiz-card ${color}"><div class="rank">${rank}</div><div class="suit">${symbol}</div></div>`;
  };

  const holeRow = (sit.hole_cards || []).map(cardHtml).join('');
  // Always render 5 board slots so layout doesn't shift between streets
  const boardCards = sit.board || [];
  let boardRow = '';
  for (let i = 0; i < 5; i++) {
    boardRow += cardHtml(boardCards[i] || null);
  }

  // Compact one-row layout: hand + arrow + board, then a single inline meta row.
  el.innerHTML = `
    <div class="quiz-situation-cards">
      <div class="quiz-cards-group">
        <div class="quiz-cards-label">your hand</div>
        <div class="quiz-cards-row">${holeRow}</div>
      </div>
      <div class="quiz-cards-group">
        <div class="quiz-cards-label">board</div>
        <div class="quiz-cards-row">${boardRow}</div>
      </div>
    </div>
    <div class="quiz-situation-meta">
      <span class="pair"><span class="label">pot</span><span class="value">${sit.pot ?? '—'}</span></span>
      <span class="pair"><span class="label">to call</span><span class="value">${sit.to_call > 0 ? sit.to_call : '—'}</span></span>
      <span class="pair"><span class="label">stack</span><span class="value">${sit.hero_stack ?? '—'}</span></span>
      <span class="pair"><span class="label">position</span><span class="value">${sit.hero_position || '—'}</span></span>
    </div>
  `;
}

function updateQuizSubmitState() {
  const data = state.pendingQuiz;
  const btn = document.getElementById('quiz-submit');
  if (!data) { btn.disabled = true; return; }
  const allAnswered = (data.questions || []).every(q => state.quizAnswers[q.id] !== undefined && state.quizAnswers[q.id] !== null);
  btn.disabled = !allAnswered;
}

function submitQuizAnswers() {
  const data = state.pendingQuiz;
  if (!data) return;
  const answers = (data.questions || []).map(q => state.quizAnswers[q.id] ?? null);
  const revealed = answers.some(a => a === '__revealed__');
  send('submit_quiz_answer', { answers, revealed });
  // Don't close yet — wait for stage_quiz_feedback to show results
  document.getElementById('quiz-submit').disabled = true;
  document.getElementById('quiz-submit').textContent = 'Checking…';
}

function onStageQuizFeedback(data) {
  // Annotate each question with correct/incorrect feedback. The detailed
  // explanations may contain HTML (<strong>, <br>, <code>) so we trust them
  // here — they come from server-side templates, not user input.
  const wrap = document.getElementById('quiz-questions');
  (data.feedback || []).forEach((fb) => {
    const qDiv = wrap.querySelector(`[data-qid="${fb.id}"]`);
    if (!qDiv) return;
    const note = document.createElement('div');
    note.className = `quiz-q-feedback ${fb.correct ? 'correct' : 'incorrect'}`;
    let html;
    if (fb.correct) {
      html = `<strong>✓ Correct.</strong><br>${fb.explanation || ''}`;
    } else if (fb.revealed) {
      html = `<strong>Revealed.</strong> Correct answer: <span class="mono">${escapeHtml(String(fb.right_answer))}</span>.<br>${fb.explanation || ''}`;
    } else {
      html = `<strong>✗ Not quite — you said ${escapeHtml(String(fb.submitted ?? ''))}.</strong><br>${fb.explanation || ''}`;
    }
    note.innerHTML = html;
    qDiv.appendChild(note);
  });

  // Update streak display
  state.streakInStage = data.correct_streak || 0;
  updateStageHUD();

  // Change button to "act now" so the user can dismiss and play
  const btn = document.getElementById('quiz-submit');
  btn.textContent = data.all_correct ? 'Nice — act now →' : 'Got it, act now →';
  btn.disabled = false;
  btn.onclick = () => {
    document.getElementById('quiz-overlay').hidden = true;
    state.pendingQuiz = null;
    // If server flagged ready_to_graduate, prompt
    if (data.ready_to_graduate) {
      showStageUpPrompt();
    }
    // Reset onclick for next round
    btn.onclick = submitQuizAnswers;
    btn.textContent = 'Submit & act';
  };
}

function showStageUpPrompt() {
  document.getElementById('stage-up-overlay').hidden = false;
}

function onStageChange(data) {
  state.stageId = data.stage_id;
  state.stageTitle = data.stage_title;
  updateStageHUD();
  // Brief intro overlay using the tour modal
  showTourSteps([{
    title: `Stage ${data.stage_id} unlocked: ${data.stage_title}`,
    body: data.stage_intro,
  }]);
}

// =============================================================
// Onboarding tour (first-visit)
// =============================================================
const TOUR_STEPS = [
  {
    title: '🚁 Welcome to the simulator',
    body: `<p>This trainer teaches Hold'em like flying a helicopter — <span class="tour-emphasis">one control axis at a time.</span></p>
           <p>Right now you control just <strong>hand reading</strong>. We'll fly outs counting, equity, pot odds, EV, position, ranges, archetypes and exploits on autopilot.</p>`,
  },
  {
    title: '🎯 Each turn, answer the question',
    body: `<p>When it's your turn we'll pop up a modal with the question for your current stage. Action buttons stay locked until you answer.</p>
           <p>Hints are available, and "Show me" reveals the answer if you're stuck (counts as wrong for the streak).</p>`,
  },
  {
    title: '🔥 Get it right, see fewer quizzes',
    body: `<p>Answer correctly and the quiz appears less often — every few turns, then occasionally. <span class="tour-emphasis">Slip up and it tightens back to every turn.</span></p>
           <p>After 5 clean hands at low frequency, we'll offer to graduate you to the next axis.</p>`,
  },
  {
    title: '📊 The stage HUD up top',
    body: `<p>Look at the row of small bars in the header. The green pulsing one is your active stage. Blue solid ones are axes you've mastered.</p>
           <p>By the time all 8 are lit, you're flying solo.</p>`,
  },
  {
    title: '🃏 Ready to deal?',
    body: `<p>Hand 1 starts now. Stage 1 is just "what hand do you have?" — easy mode while we wire up your reflexes.</p>
           <p>Have fun. The math gets you the win streak.</p>`,
  },
];
let tourIdx = 0;

function showTourSteps(steps) {
  state._customTour = steps;
  tourIdx = 0;
  renderTourStep();
  document.getElementById('tour-overlay').hidden = false;
}

function startOnboardingTour() {
  if (localStorage.getItem('thefelt_tour_done') === '1') return;
  showTourSteps(TOUR_STEPS);
}

function renderTourStep() {
  const steps = state._customTour || TOUR_STEPS;
  const step = steps[tourIdx];
  if (!step) {
    document.getElementById('tour-overlay').hidden = true;
    localStorage.setItem('thefelt_tour_done', '1');
    state._customTour = null;
    return;
  }
  document.getElementById('tour-step').innerHTML = `<h3>${step.title}</h3>${step.body}`;
  const isLast = tourIdx === steps.length - 1;
  document.getElementById('tour-next').textContent = isLast ? 'Let\'s play →' : 'Next →';
}

// =============================================================
// Public demo mode
// =============================================================
async function setupPublicDemoMode() {
  try {
    const cfg = await fetch('/api/config').then(r => r.json());
    if (!cfg.public_demo) return;
    document.getElementById('demo-banner').hidden = false;
    document.body.classList.add('demo-mode');
    // Wire up stage scrubber
    document.querySelectorAll('.demo-scrubber button').forEach((btn) => {
      btn.onclick = () => {
        const targetStage = parseInt(btn.dataset.stage, 10);
        const current = state.stageId || 1;
        if (targetStage <= current) {
          // Re-enter a stage already passed — show its walkthrough again
          localStorage.removeItem('thefelt_walkthroughs_seen');
          showWalkthroughForStage(targetStage, () => {});
        } else {
          // Graduate forward N times to reach the target
          for (let i = current; i < targetStage; i++) {
            send('graduate_stage', {});
          }
        }
        document.querySelectorAll('.demo-scrubber button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
      };
    });
    // Highlight current stage on load
    const cur = document.querySelector(`.demo-scrubber button[data-stage="${state.stageId || 1}"]`);
    if (cur) cur.classList.add('active');
  } catch (e) {
    // /api/config not reachable — that's fine, it's optional
  }
}

// =============================================================
// Init
// =============================================================
wireControls();
render();
updateStageHUD();
connect();
loadCurriculum();
startOnboardingTour();
setupPublicDemoMode();
