#!/usr/bin/env python3
"""Playwright-based blank-slate teaching agent for The Felt.

Boots a headless browser, connects to the trainer UI, and drives a Claude
agent that starts with zero poker knowledge through all 8 training stages.
The agent learns by reading on-screen coach feedback and stage walkthroughs,
then makes decisions based only on what the UI tells it.

Goals:
  1. Validate that the progressive teaching workflow actually works end-to-end.
  2. Identify where the curriculum breaks down (wrong quiz answers, confusion loops).
  3. Track win rate and bluffing effectiveness across stages.
  4. Print a structured report so the curriculum can be improved.

Usage:
  pip install playwright anthropic
  playwright install chromium
  # Start the server first:
  #   uvicorn the_felt.server.app:app --port 8000
  python tools/playwright_learner.py --url http://localhost:8000 --hands 60 --stages 1-4

Requirements:
  playwright>=1.40
  anthropic>=0.30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Optional imports — fail gracefully so the file can be read without them
# ---------------------------------------------------------------------------
try:
    from playwright.async_api import async_playwright, Page, Browser
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False

try:
    import anthropic
    ANTHROPIC_OK = True
except ImportError:
    ANTHROPIC_OK = False


# ---------------------------------------------------------------------------
# Learning stats
# ---------------------------------------------------------------------------

@dataclass
class StageStats:
    stage_id: int
    hands_played: int = 0
    hands_won: int = 0
    quiz_attempts: int = 0
    quiz_correct: int = 0
    bluff_spots: int = 0
    bluff_folds_induced: int = 0   # rough proxy from hand-stream
    decisions: list[dict] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        return self.hands_won / self.hands_played if self.hands_played else 0.0

    @property
    def quiz_accuracy(self) -> float:
        return self.quiz_correct / self.quiz_attempts if self.quiz_attempts else 0.0


@dataclass
class LearnerReport:
    total_hands: int = 0
    total_won: int = 0
    stages: dict[int, StageStats] = field(default_factory=dict)
    curriculum_gaps: list[str] = field(default_factory=list)
    confusion_log: list[str] = field(default_factory=list)
    bluff_lessons_absorbed: list[str] = field(default_factory=list)

    def stage(self, sid: int) -> StageStats:
        if sid not in self.stages:
            self.stages[sid] = StageStats(stage_id=sid)
        return self.stages[sid]

    @property
    def overall_win_rate(self) -> float:
        return self.total_won / self.total_hands if self.total_hands else 0.0

    def print(self) -> None:
        print("\n" + "=" * 65)
        print(f"PLAYWRIGHT LEARNER REPORT")
        print(f"Overall: {self.total_won}/{self.total_hands} hands won "
              f"({self.overall_win_rate:.0%} win rate)")
        print("=" * 65)

        for sid in sorted(self.stages):
            s = self.stages[sid]
            print(f"\n  Stage {sid}: {s.hands_played} hands, "
                  f"{s.win_rate:.0%} win rate, "
                  f"quiz {s.quiz_correct}/{s.quiz_attempts} "
                  f"({s.quiz_accuracy:.0%})")
            if s.bluff_spots:
                print(f"    Bluff spots seen: {s.bluff_spots}, "
                      f"apparent success: {s.bluff_folds_induced}")

        if self.curriculum_gaps:
            print("\n⚠️  CURRICULUM GAPS DETECTED:")
            for g in self.curriculum_gaps:
                print(f"   • {g}")

        if self.bluff_lessons_absorbed:
            print(f"\n✅ BLUFF LESSONS ABSORBED ({len(self.bluff_lessons_absorbed)}):")
            for l in self.bluff_lessons_absorbed[:5]:
                print(f"   • {l}")

        if self.confusion_log:
            print(f"\n📝 CONFUSION LOG (first 20):")
            for c in self.confusion_log[:20]:
                print(f"   • {c}")

        # Win-rate verdict
        print()
        if self.overall_win_rate >= 0.52:
            print(f"✅ PASS — agent winning at {self.overall_win_rate:.0%} "
                  f"(target: >50%)")
        else:
            print(f"❌ NEEDS WORK — agent at {self.overall_win_rate:.0%}, "
                  f"target >50%")

        # Identify curriculum improvements
        print("\n💡 SUGGESTED CURRICULUM IMPROVEMENTS:")
        low_quiz = [(sid, s) for sid, s in self.stages.items()
                    if s.quiz_attempts >= 5 and s.quiz_accuracy < 0.65]
        if low_quiz:
            for sid, s in low_quiz:
                print(f"   Stage {sid}: quiz accuracy {s.quiz_accuracy:.0%} — "
                      f"walkthrough needs clearer examples or more practice")
        if not self.bluff_lessons_absorbed:
            print("   No bluff lessons absorbed — introduce MDF/alpha "
                  "earlier (stage 3-4) in walkthroughs")
        if not low_quiz and self.bluff_lessons_absorbed:
            print("   (none detected — curriculum appears effective)")
        print()


# ---------------------------------------------------------------------------
# Claude decision engine
# ---------------------------------------------------------------------------

SYSTEM_BLANK_SLATE = """You are a complete beginner learning to play Texas Hold'em poker.
You know nothing about poker strategy, hand rankings, or math.
You are playing through a training app that teaches you poker step by step.

On each turn you will be given:
1. Your cards and the cards on the table
2. What the coach says about the situation
3. The available action buttons

Your job is to follow the coach's advice whenever possible.
If the coach highlights a recommended button, choose that action.
If the coach explains the situation in plain English, follow that guidance.
If you are unsure, choose the safest-seeming option (usually call if the amount is small,
fold if there is a large bet and no coach guidance).

When asked a quiz question, do your best based on what the coach has taught you so far.
Be honest — say "I don't know" rather than guessing randomly.

Always respond with a JSON object:
{
  "action": "fold" | "check" | "call" | "bet" | "raise" | "all_in",
  "reasoning": "short explanation of why"
}
Or for quiz questions:
{
  "quiz_answers": {"question_id": "your_answer"},
  "reasoning": "what I understand so far"
}
"""

async def claude_decide(
    client: "anthropic.AsyncAnthropic",
    game_state: dict,
    model: str = "claude-haiku-4-5-20251001",
) -> dict:
    """Ask Claude (blank-slate) to make a decision based on visible UI state."""
    prompt = _build_decision_prompt(game_state)
    try:
        msg = await client.messages.create(
            model=model,
            max_tokens=256,
            system=SYSTEM_BLANK_SLATE,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        # Extract JSON
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
    except Exception as e:
        pass
    # Fallback: check or call
    return {"action": "check" if game_state.get("can_check") else "call",
            "reasoning": "fallback — could not parse response"}


def _build_decision_prompt(gs: dict) -> str:
    lines = []
    lines.append(f"My cards: {gs.get('hero_cards', '?')}")
    lines.append(f"Board: {gs.get('board', 'no cards yet')}")
    lines.append(f"Pot: {gs.get('pot', 0)} chips")
    lines.append(f"To call: {gs.get('to_call', 0)} chips")
    lines.append(f"My stack: {gs.get('stack', 0)} chips")

    coach = gs.get('coach_advice', '')
    if coach:
        lines.append(f"\nCoach says: {coach}")

    rec = gs.get('recommended_button', '')
    if rec:
        lines.append(f"Coach recommends: {rec.upper()} button")

    available = gs.get('available_actions', [])
    lines.append(f"\nAvailable actions: {', '.join(available)}")
    lines.append("\nWhat action should I take?")
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Playwright game driver
# ---------------------------------------------------------------------------

class FeltLearner:
    """Drives The Felt UI via Playwright and makes decisions via Claude."""

    def __init__(self, page: Page, client: Any, report: LearnerReport,
                 model: str = "claude-haiku-4-5-20251001"):
        self.page = page
        self.client = client
        self.report = report
        self.model = model
        self.current_stage = 1
        self._hand_won = False

    async def read_game_state(self) -> dict:
        """Extract current game state from the DOM."""
        return await self.page.evaluate("""() => {
            const s = window.__state || {};
            const $ = id => document.getElementById(id);

            // Read displayed coach text
            const coachVerdict = $('coach-verdict')?.innerText || '';
            const coachWhy = $('coach-why')?.innerText || '';
            const polish = s.lastPolish || '';

            // Read visible recommended button
            let rec = '';
            ['fold','check','call','bet','raise','allin'].forEach(id => {
                const el = $('btn-' + id);
                if (el && !el.hidden && el.classList.contains('recommended')) {
                    rec = id === 'allin' ? 'all_in' : id;
                }
            });

            // Read available (visible, not hidden) action buttons
            const actions = [];
            [['fold','fold'],['check','check'],['call','call'],
             ['bet','bet'],['raise','raise'],['allin','all_in']].forEach(([id,key]) => {
                const el = $('btn-' + id);
                if (el && !el.hidden && !el.disabled) actions.push(key);
            });

            return {
                hero_cards: (s.heroCards || []).join(' '),
                board: (s.board || []).join(' '),
                pot: parseInt($('pot')?.textContent || '0') || 0,
                to_call: parseInt($('to-call')?.textContent || '0') || 0,
                stack: parseInt($('hero-stack')?.textContent || '0') || 0,
                stage_id: s.stageId || 1,
                stage_title: s.stageTitle || '',
                coach_advice: [coachVerdict, coachWhy, polish].filter(Boolean).join(' | '),
                recommended_button: rec,
                available_actions: actions,
                can_check: actions.includes('check'),
                can_call: actions.includes('call'),
                can_fold: actions.includes('fold'),
                hand_counter: s.handCounter || 0,
                net_chips: s.netChips || 0,
                hands_won: s.handsWon || 0,
                awaiting_user: s.awaitingUser || false,
                quiz_pending: !!$('quiz-overlay') && !$('quiz-overlay').hidden,
                recap_visible: !!$('recap-overlay') && !$('recap-overlay').hidden,
                stage_up_visible: !!$('stage-up-overlay') && !$('stage-up-overlay').hidden,
                walkthrough_visible: !!$('walkthrough-overlay') && !$('walkthrough-overlay').hidden,
            };
        }""")

    async def handle_walkthrough(self) -> None:
        """Click through walkthrough slides, reading each one."""
        for _ in range(20):  # max slides
            visible = await self.page.evaluate(
                "() => !document.getElementById('walkthrough-overlay').hidden"
            )
            if not visible:
                break
            # Read slide content
            body = await self.page.evaluate(
                "() => document.getElementById('walkthrough-body')?.innerText || ''"
            )
            # Check for bluff-related content
            if any(w in body.lower() for w in ['bluff', 'fold equity', 'mdf', 'alpha', 'semi-bluff']):
                self.report.bluff_lessons_absorbed.append(body[:120])
            # Click Next
            next_btn = self.page.locator('#walkthrough-next')
            skip_btn = self.page.locator('#walkthrough-skip')
            if await next_btn.is_visible():
                await next_btn.click()
                await self.page.wait_for_timeout(300)
            elif await skip_btn.is_visible():
                await skip_btn.click()
                break

    async def handle_quiz(self) -> None:
        """Read quiz questions and submit answers via Claude."""
        # Extract quiz data
        quiz_data = await self.page.evaluate("""() => {
            const overlay = document.getElementById('quiz-overlay');
            if (!overlay || overlay.hidden) return null;
            const questions = [];
            overlay.querySelectorAll('.quiz-q').forEach(q => {
                const prompt = q.querySelector('.quiz-q-prompt')?.innerText || '';
                const choices = Array.from(q.querySelectorAll('.quiz-q-choices button'))
                    .map(b => b.innerText.trim());
                const inputEl = q.querySelector('.quiz-q-input input');
                questions.push({
                    prompt,
                    choices,
                    is_numeric: !!inputEl,
                    id: q.dataset.qid || ''
                });
            });
            const stage = document.getElementById('quiz-stage-title')?.innerText || '';
            const handled = document.getElementById('quiz-handled')?.innerText || '';
            return { questions, stage, handled };
        }""")

        if not quiz_data or not quiz_data['questions']:
            # Just submit with reveal
            submit = self.page.locator('#quiz-submit')
            if await submit.is_visible():
                await submit.click()
            return

        stats = self.report.stage(self.current_stage)

        for q in quiz_data['questions']:
            stats.quiz_attempts += 1
            prompt = q['prompt']

            if q['choices']:
                # Multiple choice — ask Claude
                game_state = await self.read_game_state()
                full_prompt = (
                    f"Quiz question: {prompt}\n"
                    f"Choices: {', '.join(q['choices'])}\n"
                    f"Context: {game_state.get('coach_advice', '')[:200]}\n"
                    f"Reply with just the choice text."
                )
                try:
                    msg = await self.client.messages.create(
                        model=self.model,
                        max_tokens=64,
                        system=SYSTEM_BLANK_SLATE,
                        messages=[{"role": "user", "content": full_prompt}],
                    )
                    answer = msg.content[0].text.strip()
                    # Click the matching choice button
                    for choice_text in q['choices']:
                        if answer.lower() in choice_text.lower() or choice_text.lower() in answer.lower():
                            btn = self.page.locator(f'.quiz-q-choices button', has_text=re.compile(re.escape(choice_text[:30]), re.IGNORECASE))
                            if await btn.count() > 0:
                                await btn.first.click()
                                await self.page.wait_for_timeout(200)
                                break
                except Exception:
                    # Click first choice as fallback
                    first_btn = self.page.locator('.quiz-q-choices button').first
                    if await first_btn.count() > 0:
                        await first_btn.click()
            elif q['is_numeric']:
                # Numeric input — ask Claude for a number
                game_state = await self.read_game_state()
                full_prompt = (
                    f"Quiz: {prompt}\n"
                    f"Coach context: {game_state.get('coach_advice', '')[:200]}\n"
                    f"Reply with just the number."
                )
                try:
                    msg = await self.client.messages.create(
                        model=self.model,
                        max_tokens=16,
                        system=SYSTEM_BLANK_SLATE,
                        messages=[{"role": "user", "content": full_prompt}],
                    )
                    num_text = re.search(r'\d+\.?\d*', msg.content[0].text)
                    if num_text:
                        await self.page.locator('.quiz-q-input input').fill(num_text.group(0))
                except Exception:
                    await self.page.locator('.quiz-q-input input').fill('0')

        # Submit
        submit = self.page.locator('#quiz-submit')
        if await submit.is_visible():
            await submit.click()
            await self.page.wait_for_timeout(800)

        # Read feedback to track correct/incorrect
        feedback = await self.page.evaluate("""() => {
            const correct = document.querySelectorAll('.quiz-q-feedback.correct').length;
            const incorrect = document.querySelectorAll('.quiz-q-feedback.incorrect').length;
            return { correct, incorrect };
        }""")
        if feedback:
            stats.quiz_correct += feedback.get('correct', 0)

    async def take_action(self, gs: dict) -> None:
        """Ask Claude for a decision and click the button."""
        if not gs['available_actions']:
            return

        # Prefer the recommended button if visible
        rec = gs.get('recommended_button', '')
        if rec:
            btn_id = 'btn-allin' if rec == 'all_in' else f'btn-{rec}'
            btn = self.page.locator(f'#{btn_id}')
            if await btn.is_visible() and not await btn.get_attribute('hidden'):
                await btn.click()
                return

        # Otherwise ask Claude
        decision = await claude_decide(self.client, gs, model=self.model)
        action = decision.get('action', 'fold')

        # Track bluff spots
        coach_text = gs.get('coach_advice', '').lower()
        if any(w in coach_text for w in ['bluff', 'semi-bluff', 'fold equity', 'mdf']):
            self.report.stage(self.current_stage).bluff_spots += 1

        # Map action to button
        btn_map = {
            'fold': '#btn-fold', 'check': '#btn-check', 'call': '#btn-call',
            'bet': '#btn-bet', 'raise': '#btn-raise', 'all_in': '#btn-allin',
        }
        btn_id = btn_map.get(action, '#btn-check')
        btn = self.page.locator(btn_id)

        # Fallback chain: preferred → check → call → fold
        for fallback_id in [btn_id, '#btn-check', '#btn-call', '#btn-fold']:
            fb = self.page.locator(fallback_id)
            try:
                if await fb.is_visible():
                    attrs = await fb.evaluate("el => ({hidden: el.hidden, disabled: el.disabled})")
                    if not attrs.get('hidden') and not attrs.get('disabled'):
                        await fb.click()
                        return
            except Exception:
                continue

    async def handle_stage_up(self) -> None:
        """Always accept stage graduation."""
        go_btn = self.page.locator('#stage-up-go')
        if await go_btn.is_visible():
            await go_btn.click()
            await self.page.wait_for_timeout(500)

    async def next_hand(self) -> None:
        """Click 'Next hand' after recap."""
        btn = self.page.locator('#btn-next-hand')
        if await btn.is_visible():
            await btn.click()
            await self.page.wait_for_timeout(500)

    async def run_hand(self) -> None:
        """Play one complete hand."""
        start_counter = (await self.read_game_state()).get('hand_counter', 0)
        timeout = time.time() + 90  # 90s max per hand

        while time.time() < timeout:
            await self.page.wait_for_timeout(400)
            gs = await self.read_game_state()

            self.current_stage = gs.get('stage_id', 1)
            stats = self.report.stage(self.current_stage)

            # Handle overlays first (highest priority)
            if gs.get('walkthrough_visible'):
                await self.handle_walkthrough()
                continue

            if gs.get('quiz_pending'):
                await self.handle_quiz()
                continue

            if gs.get('stage_up_visible'):
                await self.handle_stage_up()
                continue

            if gs.get('recap_visible'):
                # Record result before moving on
                current_won = gs.get('hands_won', 0)
                self.report.total_hands += 1
                stats.hands_played += 1
                # We detect a win by checking if hand_counter changed and net went up
                # (rough heuristic — good enough for the report)
                await self.next_hand()
                return

            if gs.get('awaiting_user') and gs.get('available_actions'):
                await self.take_action(gs)
                await self.page.wait_for_timeout(600)

    async def run(self, target_hands: int) -> None:
        """Play target_hands hands, tracking stats throughout."""
        prev_won = 0
        for i in range(target_hands):
            try:
                gs_before = await self.read_game_state()
                await self.run_hand()
                gs_after = await self.read_game_state()
                # Detect win by checking hands_won counter
                new_won = gs_after.get('hands_won', 0)
                if new_won > prev_won:
                    self.report.total_won += (new_won - prev_won)
                    self.report.stage(self.current_stage).hands_won += (new_won - prev_won)
                prev_won = new_won
            except Exception as e:
                self.report.confusion_log.append(f"Hand {i+1} error: {e}")
                # Try to recover
                try:
                    await self.next_hand()
                except Exception:
                    pass
            # Brief progress
            if (i + 1) % 10 == 0:
                print(f"  [{i+1}/{target_hands}] stage={self.current_stage} "
                      f"wins={self.report.total_won} "
                      f"win_rate={self.report.overall_win_rate:.0%}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main_async(args: argparse.Namespace) -> int:
    if not PLAYWRIGHT_OK:
        print("ERROR: playwright not installed. Run: pip install playwright && playwright install chromium")
        return 1
    if not ANTHROPIC_OK:
        print("ERROR: anthropic not installed. Run: pip install anthropic")
        return 1

    api_key = args.api_key or os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set")
        return 1

    client = anthropic.AsyncAnthropic(api_key=api_key)
    report = LearnerReport()

    print(f"Starting Playwright learner → {args.url}")
    print(f"Target: {args.hands} hands, model: {args.model}")
    print()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not args.headed)
        ctx = await browser.new_context(viewport={'width': 1280, 'height': 900})
        page = await ctx.new_page()

        # Suppress console noise
        page.on('console', lambda _: None)
        page.on('pageerror', lambda _: None)

        print(f"Navigating to {args.url} ...")
        await page.goto(args.url, wait_until='networkidle', timeout=30000)
        await page.wait_for_timeout(2000)

        # Verify we connected
        status = await page.locator('#status').inner_text()
        if 'connect' not in status.lower() and 'join' not in status.lower():
            print(f"WARNING: unexpected status: {status!r}")

        learner = FeltLearner(page, client, report, model=args.model)
        await learner.run(args.hands)

        await browser.close()

    report.print()
    return 0 if report.overall_win_rate >= 0.50 else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description='Playwright blank-slate teaching agent for The Felt'
    )
    ap.add_argument('--url', default='http://localhost:8000',
                    help='URL of the running Felt server')
    ap.add_argument('--hands', type=int, default=60,
                    help='Number of hands to play')
    ap.add_argument('--model', default='claude-haiku-4-5-20251001',
                    help='Claude model to use for decisions')
    ap.add_argument('--api-key', default=None,
                    help='Anthropic API key (default: $ANTHROPIC_API_KEY)')
    ap.add_argument('--headed', action='store_true',
                    help='Run browser with UI visible (for debugging)')
    ap.add_argument('--stages', default='1-8',
                    help='Stage range to test (e.g. 1-4). Not yet enforced.')
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == '__main__':
    sys.exit(main())
