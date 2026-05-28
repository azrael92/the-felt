// SVG primitives and card rendering.

const SUIT_SYMBOLS = { h: '♥', d: '♦', s: '♠', c: '♣' };
const SUIT_COLORS  = { h: 'red',    d: 'red',    s: 'black',  c: 'black' };

const CARD_W = 44;
const CARD_H = 62;

function svgEl(tag, attrs = {}, children = []) {
  const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    el.setAttribute(k, String(v));
  }
  if (typeof children === 'string') {
    el.textContent = children;
  } else if (Array.isArray(children)) {
    for (const c of children) {
      if (c) el.appendChild(c);
    }
  }
  return el;
}

function cardFront(x, y, cardStr, opts = {}) {
  const w = opts.w || CARD_W;
  const h = opts.h || CARD_H;
  const rank = cardStr[0];
  const suit = cardStr[1];
  const color = SUIT_COLORS[suit];
  const rankDisplay = rank === 'T' ? '10' : rank;
  const g = svgEl('g', { transform: `translate(${x}, ${y})`, class: 'card' });
  g.appendChild(svgEl('rect', { x: 0, y: 0, width: w, height: h, rx: 5, ry: 5, class: 'card-rect' }));
  g.appendChild(svgEl('text', { x: 8, y: 18, class: `card-rank ${color}` }, rankDisplay));
  g.appendChild(svgEl('text', { x: 8, y: 33, class: `card-suit ${color}` }, SUIT_SYMBOLS[suit]));
  g.appendChild(svgEl('text', {
    x: w - 8, y: h - 7,
    class: `card-suit ${color}`,
    transform: `rotate(180 ${w - 8} ${h - 7})`,
  }, SUIT_SYMBOLS[suit]));
  // Centered large suit (only on bigger cards)
  if (w >= 44) {
    g.appendChild(svgEl('text', {
      x: w / 2, y: h / 2 + 8,
      class: `card-suit ${color}`,
      'text-anchor': 'middle',
      style: 'font-size: 22px; opacity: 0.18;',
    }, SUIT_SYMBOLS[suit]));
  }
  return g;
}

function cardBack(x, y, opts = {}) {
  const w = opts.w || CARD_W;
  const h = opts.h || CARD_H;
  const g = svgEl('g', { transform: `translate(${x}, ${y})`, class: 'card' });
  g.appendChild(svgEl('rect', { x: 0, y: 0, width: w, height: h, rx: 5, ry: 5, class: 'card-back' }));
  // Diamond lattice pattern
  for (let i = 6; i < w - 4; i += 7) {
    for (let j = 6; j < h - 4; j += 7) {
      g.appendChild(svgEl('rect', {
        x: i, y: j, width: 2, height: 2,
        class: 'card-back-pattern',
        transform: `rotate(45 ${i + 1} ${j + 1})`,
      }));
    }
  }
  // Center diamond
  g.appendChild(svgEl('rect', {
    x: w / 2 - 5, y: h / 2 - 5, width: 10, height: 10,
    class: 'card-back-pattern',
    style: 'opacity: 0.6;',
    transform: `rotate(45 ${w / 2} ${h / 2})`,
  }));
  return g;
}

function cardSlot(x, y, opts = {}) {
  const w = opts.w || CARD_W;
  const h = opts.h || CARD_H;
  const g = svgEl('g', { transform: `translate(${x}, ${y})` });
  g.appendChild(svgEl('rect', {
    x: 0, y: 0, width: w, height: h, rx: 5, ry: 5,
    fill: 'rgba(0, 0, 0, 0.22)',
    stroke: 'rgba(255, 255, 255, 0.05)',
  }));
  return g;
}

// Render a tiny stack of chips representing a chip count.
// We use up to 4 "stacks" of decreasing denomination.
function chipStack(cx, cy, amount) {
  const g = svgEl('g', { transform: `translate(${cx}, ${cy})` });
  if (amount <= 0) return g;
  // Denominations: 500 (gold), 100 (accent), 25 (red), 5 (gray)
  const denoms = [
    { v: 500, color: 'large', max: 4 },
    { v: 100, color: '', max: 5 },
    { v: 25,  color: 'small', max: 5 },
  ];
  let remaining = amount;
  let xOffset = 0;
  for (const { v, color, max } of denoms) {
    const n = Math.min(max, Math.floor(remaining / v));
    if (n === 0) continue;
    for (let i = 0; i < n; i++) {
      g.appendChild(svgEl('ellipse', {
        cx: xOffset, cy: -i * 2, rx: 7, ry: 2.2,
        class: `stack-chip ${color}`,
      }));
    }
    remaining -= n * v;
    xOffset += 18;
  }
  return g;
}

// SVG defs (gradients used across the table).
function defs() {
  const d = svgEl('defs');
  // Felt radial gradient — warm dark, subdued center
  const fg = svgEl('radialGradient', { id: 'felt-gradient', cx: '50%', cy: '50%', r: '55%' });
  fg.appendChild(svgEl('stop', { offset: '0%', 'stop-color': '#132b1e' }));
  fg.appendChild(svgEl('stop', { offset: '60%', 'stop-color': '#0c1f14' }));
  fg.appendChild(svgEl('stop', { offset: '100%', 'stop-color': '#070f09' }));
  d.appendChild(fg);
  // Card back gradient — dark amethyst
  const cbg = svgEl('linearGradient', { id: 'card-back-gradient', x1: '0%', y1: '0%', x2: '100%', y2: '100%' });
  cbg.appendChild(svgEl('stop', { offset: '0%', 'stop-color': '#3a2860' }));
  cbg.appendChild(svgEl('stop', { offset: '100%', 'stop-color': '#1a1030' }));
  d.appendChild(cbg);
  return d;
}

window.SmokerCards = { svgEl, cardFront, cardBack, cardSlot, chipStack, defs, CARD_W, CARD_H };
