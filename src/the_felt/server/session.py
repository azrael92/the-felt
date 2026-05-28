"""One Session = one connected user playing hands against archetyped bots.

Phase 2/3 wires up:
- Multiple archetype bots (assigned by `skill.adapter.difficulty_for`)
- Archetype-aware coach math (`coach.analyzer`)
- Tier-filtered explanations (`coach.explain.render_tier`)
- LLM polish + Q&A (`coach.llm_polish`)
- Glicko-2 rating updates (`skill.tracker`)
- Decision scoring + leak detection (`skill.evaluator` + `skill.leak`)
- SQLite persistence (`persistence.store`)
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Any

from the_felt.agents.archetype import REGISTRY, TAG, Archetype
from the_felt.agents.policy import decide as decide_action
from the_felt.agents.texture import analyze as analyze_texture
from the_felt.cards import Deck, card, to_str
from the_felt.coach.analyzer import DecisionContext, compute_decision_context
from the_felt.coach.derivation import derive as derive_math
from the_felt.coach.explain import render_tier
from the_felt.coach.llm_polish import answer_question, polish
from the_felt.coach.narrator import (
    narrate_board,
    narrate_postflop_action,
    narrate_preflop_action,
)
from the_felt.cards import card as parse_card
from the_felt.curriculum.drills import Drill, generate as generate_drill, is_correct as drill_is_correct
from the_felt.curriculum.lessons import get_lesson, relevance_for_ctx
from the_felt.curriculum.stages import (
    StageState,
    build_quiz_for_decision,
    detailed_feedback,
    get_stage,
    get_walkthrough,
    grade_answer,
    next_stage,
    should_quiz,
)
from the_felt.eval import describe as describe_hand
from the_felt.probability.outs import outs_breakdown
from the_felt.engine.hand import ActionRequest, Hand
from the_felt.engine.table import Player, Table
from the_felt.persistence.store import Store
from the_felt.server.protocol import (
    ActionToActData,
    BoardData,
    CoachMath,
    CoachTipData,
    HandEndData,
    HandStartData,
    LegalActionsView,
    PlayerActionData,
    SeatInfo,
)
from the_felt.skill.adapter import difficulty_for
from the_felt.skill.evaluator import score_decision
from the_felt.skill.leak import detect_leaks
from the_felt.skill.tracker import Glicko2Rating, GlickoTracker
from the_felt.types import Action, ActionType, Street

log = logging.getLogger(__name__)

BOT_NAMES = ["Nora", "Tess", "Lyle", "Cody", "Jess", "Adam", "Riley", "Kim", "Sam"]


def _verdict_to_button(verdict_key: str) -> str:
    """Map an internal verdict key to the UI button it corresponds to."""
    if verdict_key == "fold": return "fold"
    if verdict_key == "check": return "check"
    if verdict_key == "call": return "call"
    if verdict_key == "bet_value": return "bet"
    if verdict_key in ("raise_min", "raise_big"): return "raise"
    return ""


@dataclass
class Session:
    user_id: str
    user_name: str
    seats: int
    sb: int
    bb: int
    stack_bb: int
    store: Store | None = None
    # State
    table: Table | None = None
    hand: Hand | None = None
    hand_counter: int = 0
    rng: random.Random = field(default_factory=random.Random)
    # Coach state
    current_ctx: DecisionContext | None = None
    current_tier: int = 1
    user_tier_override: int | None = None
    # Skill state
    ratings: dict[str, Glicko2Rating] = field(default_factory=dict)
    top_leak: str | None = None
    # Streak: consecutive good decisions
    streak_current: int = 0
    streak_longest: int = 0
    # Curriculum
    active_lesson_id: str | None = None
    # Pilot-style training stage state
    stage_state: StageState = field(default_factory=StageState)
    # Pending quiz: when a quiz is dispatched we wait for the submitted answers
    # before unlocking the action buttons.
    _pending_quiz: dict | None = None
    _quiz_answers: asyncio.Queue = field(default_factory=asyncio.Queue)
    # Track most recent drill issued so we can score the answer
    _last_drill: Drill | None = None
    # Streak gaming guard: only one streak increment per hand
    _streak_credited_this_hand: bool = False
    # Queues
    outbound: asyncio.Queue = field(default_factory=asyncio.Queue)
    user_action: asyncio.Queue = field(default_factory=asyncio.Queue)
    ask_coach_q: asyncio.Queue = field(default_factory=asyncio.Queue)
    drill_q: asyncio.Queue = field(default_factory=asyncio.Queue)
    next_hand_signal: asyncio.Event = field(default_factory=asyncio.Event)
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    # Bot archetypes by seat id
    bot_archetypes: dict[str, Archetype] = field(default_factory=dict)
    # Per-decision evaluation queue (one per hand)
    pending_evaluations: list[tuple[DecisionContext, Action, str]] = field(default_factory=list)
    # Background tasks
    _qa_task: asyncio.Task | None = None

    # ---------- setup ----------

    async def setup(self) -> None:
        """Initial load: ratings, leak detection, tier.

        Wrapped with timeouts and graceful degradation: if the store is broken
        (stale file handle, corrupt DB, etc.), the session continues with
        default ratings rather than hanging the hand loop.
        """
        for cat in ("overall", "preflop", "flop", "turn", "river"):
            self.ratings[cat] = Glicko2Rating()
        self.top_leak = None

        if self.store:
            try:
                await asyncio.wait_for(self._load_from_store(), timeout=3.0)
            except (asyncio.TimeoutError, Exception):
                log.exception("store load failed — proceeding with default ratings")
                # Disable further store writes to avoid recurring hangs
                self.store = None
        self._update_tier()

    async def _load_from_store(self) -> None:
        await self.store.ensure_user(self.user_id, self.user_name)
        for cat in ("overall", "preflop", "flop", "turn", "river"):
            self.ratings[cat] = await self.store.get_rating(self.user_id, cat)
        recent = await self.store.recent_decisions(self.user_id, limit=100)
        leak = detect_leaks(recent)
        self.top_leak = leak.top_leak
        self.streak_current, self.streak_longest = await self.store.get_streak(self.user_id)

    def _update_tier(self) -> None:
        # Explanation tier is user-controlled — it gates the COACH'S language
        # (plain English at T1 vs. poker jargon at T2+) and is independent of
        # the opponent difficulty profile, which adapts automatically.
        #
        # Default to T1 (beginner) until the user opts in to a higher tier via
        # the selector. This guarantees a novice never sees "equity" / "MDF" /
        # "α" until they've decided they want it.
        if self.user_tier_override is not None:
            self.current_tier = self.user_tier_override
            return
        self.current_tier = 1

    def build_table(self) -> None:
        """Construct the table with archetyped bots per the difficulty profile."""
        starting_stack = self.bb * self.stack_bb
        profile = difficulty_for(self.ratings["overall"].mu, leak_tag=self.top_leak)
        n_bots = self.seats - 1
        archetypes = profile.assign_archetypes(n_bots, self.rng)

        players: list[Player] = [
            Player(id=self.user_id, name=self.user_name, seat=0, stack=starting_stack, is_bot=False),
        ]
        names_shuffled = BOT_NAMES.copy()
        self.rng.shuffle(names_shuffled)
        for i in range(1, self.seats):
            arch = archetypes[i - 1]
            display = f"{names_shuffled[(i-1) % len(names_shuffled)]} ({arch.name})"
            p = Player(
                id=f"b{i}", name=display, seat=i, stack=starting_stack,
                is_bot=True, archetype_name=arch.name,
            )
            players.append(p)
            self.bot_archetypes[p.id] = arch

        self.table = Table(players=players, button_seat=0, sb=self.sb, bb=self.bb)

    # ---------- main loop ----------

    async def run(self) -> None:
        await self.setup()
        self.build_table()
        try:
            # Start Q&A handler in the background
            self._qa_task = asyncio.create_task(self._qa_loop())
            # Start drill handler in the background
            self._drill_task = asyncio.create_task(self._drill_loop())

            while not self.closed.is_set():
                if self.table.num_active() < 2:
                    await self._emit("error", {"code": "table_dead", "message": "Not enough chips to continue."})
                    break

                # Clear next_hand signal BEFORE we start this hand to avoid
                # set-then-clear race with the client's eager request.
                self.next_hand_signal.clear()

                self.hand_counter += 1
                hand_id = f"h_{self.hand_counter}"
                deck = Deck()
                # Snapshot hero's stack BEFORE blinds are posted in hand.start()
                hero_player = next(p for p in self.table.players if not p.is_bot)
                self._hero_stack_at_hand_start = hero_player.stack
                self.hand = Hand(self.table, deck, hand_id)
                self.hand.start()
                self.pending_evaluations = []
                # Reset per-hand streak guard
                self._streak_credited_this_hand = False
                # Roll over the per-hand "clean" tracking for the training stage
                self.stage_state.reset_hand()

                await self._emit_hand_start()
                await self._flush_history_events(start_seq=0)
                await self._play_hand()

                # Persist hand (best-effort)
                if self.store and self.hand.result is not None:
                    try:
                        await asyncio.wait_for(
                            self.store.save_hand(
                                hand_id=self.hand.hand_id,
                                user_id=self.user_id,
                                events=[{"seq": e.seq, "kind": e.kind, "data": e.data} for e in self.hand.history.events],
                                result={
                                    "winners": self.hand.result.winners,
                                    "showdown": self.hand.result.showdown,
                                },
                            ),
                            timeout=2.0,
                        )
                    except Exception:
                        log.exception("save_hand failed — disabling store")
                        self.store = None

                # Recompute leak + tier after batch of decisions (best-effort)
                if self.store:
                    try:
                        recent = await asyncio.wait_for(
                            self.store.recent_decisions(self.user_id, limit=100),
                            timeout=2.0,
                        )
                        leak = detect_leaks(recent)
                        self.top_leak = leak.top_leak
                        self._update_tier()
                        await self._emit("leak_report", {
                            "top_leak": leak.top_leak,
                            "top_leak_count": leak.top_leak_count,
                            "total": leak.total_decisions,
                            "blunder_rate": leak.blunder_rate,
                            "leak_rate": leak.leak_rate,
                            "counts": leak.counts,
                        })
                    except Exception:
                        log.exception("recent_decisions failed — disabling store")
                        self.store = None

                # Wait for next-hand signal (it may already be set if the
                # client sent next_hand between hand_end and now).
                done, _ = await asyncio.wait(
                    [
                        asyncio.create_task(self.next_hand_signal.wait()),
                        asyncio.create_task(self.closed.wait()),
                    ],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if self.closed.is_set():
                    break

                # Advance button + rebuy any broke players. Possibly re-seat archetypes.
                self.table.advance_button()
                for p in self.table.players:
                    if p.stack <= 0:
                        p.stack = self.bb * self.stack_bb  # auto-rebuy
                # Re-roll difficulty / archetypes every 10 hands
                if self.hand_counter % 10 == 0:
                    self.build_table()
                    await self._emit("difficulty_update", {
                        "rating": self.ratings["overall"].mu,
                        "tier": self.current_tier,
                        "top_leak": self.top_leak,
                    })
        except Exception:
            log.exception("Session run loop crashed")
            await self._emit("error", {"code": "internal", "message": "Server error"})
        finally:
            if self._qa_task:
                self._qa_task.cancel()
            drill_task = getattr(self, "_drill_task", None)
            if drill_task:
                drill_task.cancel()

    async def _play_hand(self) -> None:
        assert self.hand is not None
        last_emitted = self.hand.history._next_seq

        while not self.hand.is_complete:
            req = self.hand.next_action_request()
            if req is None:
                break
            actor = self.table.players[req.seat]

            if actor.is_bot:
                # Brief "thinking" delay for UX
                await asyncio.sleep(0.3 + self.rng.random() * 0.4)
                arch = self.bot_archetypes.get(actor.id, TAG)
                action = decide_action(self.hand, req, arch, rng=self.rng)
                self.hand.apply(action)
                await self._flush_history_events(start_seq=last_emitted)
                last_emitted = self.hand.history._next_seq
                continue

            # Human's turn — compute coach context FIRST (the quiz needs the
            # equity/EV numbers as "correct" answers).
            ctx = await self._emit_coach_tip(req)
            self.current_ctx = ctx
            polish_task = asyncio.create_task(self._stream_polish(req, ctx))

            # Pilot-style quiz: if dice rolls in for this stage, send the modal
            # FIRST and block on the answer before unlocking action buttons.
            quiz_passed = await self._maybe_run_stage_quiz(req, ctx)

            # Now the action_to_act fires — UI shows action buttons.
            await self._emit_action_to_act(req)

            # Wait for matching user action
            while True:
                payload = await self.user_action.get()
                if payload.get("hand_id") != self.hand.hand_id:
                    await self._emit("error", {"code": "stale_hand", "message": "Wrong hand id"})
                    continue
                if payload.get("seq") != req.seq:
                    await self._emit("error", {"code": "stale_seq", "message": "Stale action sequence"})
                    continue
                break

            try:
                action = self._payload_to_action(payload, req)
                # Score BEFORE applying so we have the pre-action state context
                score = score_decision(action, ctx, bb=self.table.bb)
                # Persist + update Glicko (best-effort — never blocks the hand)
                await self._record_decision(req.street.value, ctx, action, score)
                # Update streak based on the decision bucket
                streak_changed = await self._update_streak(score.bucket)
                # Award live-decision drill credit toward relevant modules
                await self._award_live_drill_credit(ctx, score.bucket)
                # Send rating update (streak piggybacks here)
                await self._emit("rating_update", {
                    "category": req.street.value,
                    "mu": self.ratings[req.street.value].mu,
                    "phi": self.ratings[req.street.value].phi,
                    "overall_mu": self.ratings["overall"].mu,
                    "delta_ev_bb": score.delta_ev_bb,
                    "bucket": score.bucket,
                    "leak_tag": score.leak_tag,
                    "ideal_action": score.ideal_action,
                    "user_action": score.user_action,
                    "streak": {
                        "current": self.streak_current,
                        "longest": self.streak_longest,
                        "changed": streak_changed,
                    },
                })
                self.hand.apply(action)
            except ValueError as e:
                # Illegal action (e.g. raise below min): tell the user and let them retry
                await self._emit("error", {"code": "illegal", "message": str(e)})
                req2 = self.hand.next_action_request()
                if req2 is not None:
                    await self._emit_action_to_act(req2)
                continue
            except Exception as e:
                # Any other unexpected error (DB issue, bug) — log it server-side
                # but DON'T show it to the user as "illegal action". Just let
                # the hand continue if possible.
                log.exception("internal error during action apply")
                if not self.hand.is_complete:
                    req2 = self.hand.next_action_request()
                    if req2 is not None:
                        await self._emit_action_to_act(req2)
                continue
            finally:
                if not polish_task.done():
                    polish_task.cancel()

            await self._flush_history_events(start_seq=last_emitted)
            last_emitted = self.hand.history._next_seq

        await self._emit_hand_end()

    async def _maybe_run_stage_quiz(self, req: ActionRequest, ctx: DecisionContext) -> bool:
        """Fire the per-turn training quiz if the stage's adaptive frequency
        dice rolls in. Block on the user's submitted answers, grade them,
        update streak. Returns True iff all answers correct (or quiz skipped).
        """
        # Build a small dict the stage builder can consume
        hero = next(p for p in self.table.players if not p.is_bot)
        hero_pos = hero.position.value if hero.position else None
        # IP heuristic: hero is in position vs the last aggressor iff hero
        # acts last on this street. For now treat BTN/CO as IP.
        in_position = hero_pos in ("BTN", "CO")
        ev_labels = ctx.ev_labels or {}
        ev_actions = ctx.ev_by_action or {}
        sorted_evs = sorted(ev_actions.items(), key=lambda kv: -kv[1])
        ev_labels_ranked = [
            f"{ev_labels.get(k, k)} ({v:+.1f})" for k, v in sorted_evs
        ]
        hand_class_label = self._describe_hand_class(hero.hole_cards, self.hand.board)
        decision_ctx = {
            "to_call": ctx.to_call,
            "outs": ctx.outs,
            "spot": ctx.spot,
            "street": ctx.street,
            "villain_archetype": ctx.villain_archetype,
            "villain_range_size": ctx.villain_range_size,
            "equity_pct": ctx.equity * 100,
            "pot_odds_required_pct": ctx.pot_odds_required * 100,
            "edge": ctx.edge,
            "ev_call": ev_actions.get("call", 0.0),
            "ev_labels_ranked": ev_labels_ranked,
            "hero_position": hero_pos,
            "in_position": in_position,
            "verdict_label": ctx.verdict_label,
            "hand_class_label": hand_class_label,
            # The student needs to SEE these to answer the questions.
            "hole_cards": [to_str(c) for c in hero.hole_cards],
            "board": [to_str(c) for c in self.hand.board],
            "pot": req.pot,
            "hero_stack": hero.stack,
            # Categorized outs (for detailed wrong-answer feedback)
            "outs_breakdown": outs_breakdown(hero.hole_cards, self.hand.board),
        }

        if not should_quiz(self.stage_state, decision_ctx, self.rng):
            return True   # skipped (frequency low or trivial spot)

        # Build the quiz and dispatch
        quiz = build_quiz_for_decision(self.stage_state, decision_ctx)
        # Hold the canonical "correct" answers server-side for grading
        self._pending_quiz = quiz
        await self._emit("stage_quiz", {
            "hand_id": self.hand.hand_id if self.hand else "",
            "seq": req.seq,
            "stage_id": quiz["stage_id"],
            "stage_title": quiz["stage_title"],
            "stage_teaches": quiz["stage_teaches"],
            "questions": [
                # Don't ship the "correct" answer to the client (cheating prevention)
                {k: v for k, v in q.items() if k != "correct"}
                for q in quiz["questions"]
            ],
            "handled_for_you": list(quiz["handled_for_you"]),
            "handled_summary": quiz["handled_summary"],
            "frequency": quiz["frequency"],
            "correct_streak": quiz["correct_streak"],
            "clean_hands": quiz["clean_hands"],
            # The "situation card" rendered at the top of the modal so the
            # student can see what they're being asked about.
            "situation": {
                "hole_cards": decision_ctx["hole_cards"],
                "board": decision_ctx["board"],
                "pot": decision_ctx["pot"],
                "to_call": decision_ctx["to_call"],
                "hero_position": decision_ctx["hero_position"],
                "hero_stack": decision_ctx["hero_stack"],
                "street": decision_ctx["street"],
            },
        })

        # Wait for the client to submit answers — block until we get them OR
        # the user picks "show me" (revealed) on every question.
        try:
            submission = await asyncio.wait_for(self._quiz_answers.get(), timeout=180.0)
        except asyncio.TimeoutError:
            # Treat a 3-minute no-response as a skip (don't penalize)
            self._pending_quiz = None
            return True

        # Grade each answer + generate concept-aware feedback
        all_correct = True
        feedback = []
        revealed_any = bool(submission.get("revealed"))
        answers = submission.get("answers", [])
        for q, ans in zip(quiz["questions"], answers):
            if ans is None or ans == "__revealed__":
                self.stage_state.record_quiz_result(False)
                all_correct = False
                feedback.append({
                    "id": q["id"], "correct": False, "revealed": True,
                    "right_answer": q.get("correct"),
                    "explanation": detailed_feedback(q, ans, decision_ctx),
                })
                continue
            ok = grade_answer(q, ans)
            self.stage_state.record_quiz_result(ok)
            if not ok:
                all_correct = False
            feedback.append({
                "id": q["id"], "correct": ok, "revealed": False,
                "submitted": ans,
                "right_answer": q.get("correct"),
                # On correct, just confirm; on wrong, show the breakdown.
                "explanation": (
                    q.get("formula") or q.get("hint", "")
                    if ok else detailed_feedback(q, ans, decision_ctx)
                ),
            })

        # Send feedback + updated state back to the UI
        await self._emit("stage_quiz_feedback", {
            "hand_id": self.hand.hand_id if self.hand else "",
            "seq": req.seq,
            "all_correct": all_correct,
            "feedback": feedback,
            "correct_streak": self.stage_state.correct_streak,
            "clean_hands": self.stage_state.clean_hands,
            "frequency": self.stage_state.frequency(),
            "ready_to_graduate": self.stage_state.ready_to_graduate(),
            "stage_id": self.stage_state.stage_id,
        })

        # Auto-clear the pending quiz so the next decision can quiz fresh
        self._pending_quiz = None
        return all_correct

    def _describe_hand_class(self, hole: list, board: list) -> str:
        """Return human label like 'Pair' / 'Two pair' for the current hand."""
        # Pre-flop: just say "Pre-flop" since there's no made hand yet
        if len(board) < 3:
            from the_felt.cards import rank_of
            ranks = sorted([rank_of(c) for c in hole], reverse=True)
            if ranks[0] == ranks[1]:
                return "Pair"
            return "High card"
        desc = describe_hand(hole, board)
        # Normalize to our 9-choice list
        norm_map = {
            "Royal Flush": "Straight flush",
            "Straight Flush": "Straight flush",
            "Four of a Kind": "Four of a kind",
            "Full House": "Full house",
            "Flush": "Flush",
            "Straight": "Straight",
            "Three of a Kind": "Three of a kind",
            "Two Pair": "Two pair",
            "Pair": "Pair",
            "High Card": "High card",
        }
        return norm_map.get(desc, desc)

    async def _stream_polish(self, req: ActionRequest, ctx: DecisionContext) -> None:
        try:
            text = await polish(ctx, self.current_tier)
            await self._emit("coach_tip_polish", {
                "hand_id": self.hand.hand_id,
                "seq": req.seq,
                "tier": self.current_tier,
                "text": text,
            })
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("polish failed")

    async def _drill_loop(self) -> None:
        """Handle drill requests: generate a drill, ship it; accept the answer,
        score it, persist, return feedback."""
        while not self.closed.is_set():
            try:
                payload = await asyncio.wait_for(self.drill_q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            kind = payload.get("kind")  # "start_drill" | "submit_drill_answer" | "set_active_lesson"
            try:
                if kind == "start_drill":
                    drill_kind = payload.get("drill_kind")
                    lesson_id = payload.get("lesson_id")
                    if not drill_kind:
                        await self._emit("error", {"code": "bad_drill", "message": "drill_kind required"})
                        continue
                    drill = generate_drill(drill_kind, rng=self.rng)
                    self._last_drill = drill
                    await self._emit("drill_question", {
                        "lesson_id": lesson_id or drill.lesson_id,
                        "kind": drill.kind,
                        "question": drill.question,
                        "answer_type": drill.answer_type,
                        "choices": drill.choices,
                        "context": drill.context,
                    })
                elif kind == "submit_drill_answer":
                    submitted = payload.get("answer")
                    drill = self._last_drill
                    if drill is None:
                        await self._emit("error", {"code": "no_drill", "message": "no drill in progress"})
                        continue
                    correct = drill_is_correct(drill, submitted)
                    # Persist
                    if self.store:
                        try:
                            await asyncio.wait_for(
                                self.store.record_drill_attempt(
                                    self.user_id, drill.lesson_id, drill.kind, correct,
                                ),
                                timeout=2.0,
                            )
                        except Exception:
                            log.exception("record_drill_attempt failed (non-fatal)")
                    await self._emit("drill_feedback", {
                        "lesson_id": drill.lesson_id,
                        "kind": drill.kind,
                        "correct": correct,
                        "correct_answer": (
                            drill.choices[drill.correct_index] if drill.answer_type == "mc" and drill.correct_index >= 0
                            else str(drill.answer)
                        ),
                        "explanation": drill.explanation,
                    })
                    self._last_drill = None
                elif kind == "set_active_lesson":
                    self.active_lesson_id = payload.get("lesson_id")
            except Exception:
                log.exception("drill_loop op failed")

    async def _qa_loop(self) -> None:
        """Background loop that answers user follow-up questions."""
        while not self.closed.is_set():
            try:
                payload = await asyncio.wait_for(self.ask_coach_q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            question = payload.get("question", "")
            if not question or self.current_ctx is None:
                continue
            try:
                answer = await answer_question(question, self.current_ctx, self.current_tier)
                await self._emit("coach_answer", {
                    "hand_id": self.hand.hand_id if self.hand else None,
                    "question": question,
                    "answer": answer,
                })
            except Exception:
                log.exception("qa failed")

    async def _record_decision(
        self,
        street: str,
        ctx: DecisionContext,
        action: Action,
        score,
    ) -> None:
        # Update per-street rating and overall rating
        for cat in (street, "overall"):
            r = self.ratings[cat]
            new_r = GlickoTracker.from_delta_ev(r, score.delta_ev_bb)
            self.ratings[cat] = new_r
        # Best-effort persistence — never block the hand loop on it
        if self.store:
            try:
                await asyncio.wait_for(self._persist_decision(street, ctx, score), timeout=2.0)
            except Exception:
                log.exception("decision persist failed — disabling store")
                self.store = None

    def _compute_concept_focus(self, ctx: DecisionContext) -> dict | None:
        """If the user has an active lesson AND this live spot exercises its
        module, return a focus hint dict; else None."""
        if not self.active_lesson_id:
            return None
        from the_felt.curriculum.lessons import get_lesson, get_module
        lesson = get_lesson(self.active_lesson_id)
        if lesson is None:
            return None
        # Build a context dict for relevance check
        rel = relevance_for_ctx({
            "to_call": ctx.to_call,
            "outs": ctx.outs,
            "spot": ctx.spot,
            "street": ctx.street,
            "villain_archetype": ctx.villain_archetype,
        })
        if lesson.module_id not in rel:
            return None
        module = get_module(lesson.module_id)
        return {
            "module_id": lesson.module_id,
            "module_title": module.title if module else lesson.module_id,
            "lesson_id": lesson.id,
            "hint": f"Practice {lesson.title.lower()} — this spot exercises that concept.",
        }

    async def _award_live_drill_credit(self, ctx: DecisionContext, bucket: str) -> None:
        """Live decisions count toward module mastery. `great|fine` → correct;
        `minor_leak|blunder` → incorrect. Logged once per relevant module per
        decision."""
        if not self.store:
            return
        # Only count clear outcomes (skip neutral or unclear)
        correct = bucket in ("great", "fine")
        if bucket not in ("great", "fine", "minor_leak", "blunder"):
            return
        rel = relevance_for_ctx({
            "to_call": ctx.to_call,
            "outs": ctx.outs,
            "spot": ctx.spot,
            "street": ctx.street,
            "villain_archetype": ctx.villain_archetype,
        })
        # Record one attempt per relevant module, tagged drill_kind="live"
        for module_id in rel:
            from the_felt.curriculum.lessons import get_module
            module = get_module(module_id)
            if module is None or not module.lessons:
                continue
            # Credit goes to the first lesson in the module as a representative
            lesson_id = module.lessons[0].id
            try:
                await asyncio.wait_for(
                    self.store.record_drill_attempt(
                        self.user_id, lesson_id, "live", correct,
                    ),
                    timeout=1.0,
                )
            except Exception:
                log.exception("live drill credit failed (non-fatal)")

    async def _update_streak(self, bucket: str) -> str | None:
        """Increment, reset, or no-op the streak based on decision bucket.

        Invariants:
            great|fine → increment (capped at 1 per hand to prevent gaming)
            blunder   → reset to 0
            minor_leak → no-op (preserves streak on borderline spots)

        Returns "incremented" | "reset" | None.
        """
        change: str | None = None
        if bucket == "blunder":
            self.streak_current = 0
            change = "reset"
            if self.store:
                try:
                    await asyncio.wait_for(self.store.reset_streak(self.user_id), timeout=2.0)
                except Exception:
                    log.exception("reset_streak failed (non-fatal)")
        elif bucket in ("great", "fine") and not self._streak_credited_this_hand:
            self.streak_current += 1
            self.streak_longest = max(self.streak_longest, self.streak_current)
            self._streak_credited_this_hand = True
            change = "incremented"
            if self.store:
                try:
                    await asyncio.wait_for(self.store.bump_streak(self.user_id), timeout=2.0)
                except Exception:
                    log.exception("bump_streak failed (non-fatal)")
        # minor_leak and second-good-decision-in-same-hand → no-op
        return change

    async def _persist_decision(self, street: str, ctx: DecisionContext, score) -> None:
        for cat in (street, "overall"):
            await self.store.save_rating(self.user_id, cat, self.ratings[cat])
        await self.store.log_decision(
            user_id=self.user_id,
            hand_id=self.hand.hand_id if self.hand else "",
            street=street,
            state={
                "hero_cards": ctx.hero_cards,
                "board": ctx.board,
                "pot": ctx.pot,
                "to_call": ctx.to_call,
                "equity": ctx.equity,
                "villain_archetype": ctx.villain_archetype,
                "verdict": ctx.verdict,
                "ev_by_action": ctx.ev_by_action,
            },
            user_action=score.user_action,
            ideal_action=score.ideal_action,
            delta_ev=score.delta_ev,
            delta_ev_bb=score.delta_ev_bb,
            bucket=score.bucket,
            leak_tag=score.leak_tag,
        )

    def _payload_to_action(self, payload: dict, req: ActionRequest) -> Action:
        a = payload["action"]
        amount = int(payload.get("amount", 0))
        kind = ActionType(a)
        if kind in (ActionType.FOLD, ActionType.CHECK):
            return Action(kind, 0)
        if kind == ActionType.CALL:
            return Action(ActionType.CALL, req.to_call)
        return Action(kind, amount)

    # ---------- emit helpers ----------

    async def _emit(self, msg_type: str, data: dict[str, Any]) -> None:
        await self.outbound.put({"type": msg_type, "v": 1, "data": data})

    async def _emit_hand_start(self) -> None:
        assert self.hand and self.table
        hero = next(p for p in self.table.players if not p.is_bot)
        seats = [
            SeatInfo(
                seat=p.seat, id=p.id, name=p.name, stack=p.stack,
                position=p.position.value if p.position else None,
                is_bot=p.is_bot,
                archetype=p.archetype_name,
            ).model_dump()
            for p in self.table.players
        ]
        data = HandStartData(
            hand_id=self.hand.hand_id,
            button_seat=self.table.button_seat,
            sb=self.table.sb,
            bb=self.table.bb,
            seats=seats,
            hero_seat=hero.seat,
            hero_cards=[to_str(c) for c in hero.hole_cards],
            hero_stack_before_blinds=self._hero_stack_at_hand_start,
        ).model_dump()
        await self._emit("hand_start", data)

    async def _flush_history_events(self, start_seq: int) -> None:
        assert self.hand is not None
        hero = next(p for p in self.table.players if not p.is_bot)
        for ev in self.hand.history.events:
            if ev.seq < start_seq:
                continue
            if ev.kind == "hand_start" or ev.kind == "deal_hole":
                continue
            if ev.kind == "post_blind":
                await self._emit("post_blind", {"hand_id": self.hand.hand_id, **ev.data})
            elif ev.kind == "player_action":
                await self._emit("player_action", PlayerActionData(
                    hand_id=self.hand.hand_id,
                    seq=ev.seq,
                    seat=ev.data["seat"],
                    player_id=ev.data["player_id"],
                    street=ev.data["street"],
                    action=ev.data["action"],
                    amount=ev.data["amount"],
                    stack_after=ev.data["stack_after"],
                    committed_street_after=ev.data["committed_street_after"],
                    pot_after=ev.data["pot_after"],
                ).model_dump())
                # Narrate the action (only if hero is still live in the hand)
                if not hero.is_folded and ev.data["player_id"] != hero.id:
                    await self._narrate_player_action(ev)
            elif ev.kind == "board":
                await self._emit("board", BoardData(
                    hand_id=self.hand.hand_id,
                    street=ev.data["street"],
                    new_cards=ev.data["new_cards"],
                    board=ev.data["board"],
                    pot=ev.data["pot"],
                ).model_dump())
                if not hero.is_folded:
                    await self._narrate_board(ev)

    async def _narrate_player_action(self, ev) -> None:
        """Generate a teaching narration for a non-hero action."""
        d = ev.data
        seat_idx = d["seat"]
        actor = self.table.players[seat_idx]
        arch = self.bot_archetypes.get(actor.id)
        if arch is None:
            return

        # Count raises before this action this street
        street = d["street"]
        raises_before = 0
        for prior in self.hand.history.events:
            if prior.seq >= ev.seq:
                break
            if prior.kind != "player_action":
                continue
            if prior.data.get("street") != street:
                continue
            if prior.data.get("action") in ("raise", "bet"):
                raises_before += 1

        try:
            if street == "preflop":
                facing_raise = raises_before >= 1
                note = narrate_preflop_action(
                    actor=actor,
                    arch=arch,
                    action=d["action"],
                    amount=d["amount"],
                    bb=self.table.bb,
                    raises_before=raises_before,
                    facing_raise=facing_raise,
                )
            else:
                pfa = None
                for prior in self.hand.history.events:
                    if prior.kind == "player_action" and prior.data.get("street") == "preflop":
                        if prior.data["action"] in ("raise", "bet"):
                            pfa = prior.data["player_id"]
                # self.hand.board is already a list of Treys ints — don't re-parse
                texture = analyze_texture(self.hand.board)
                note = narrate_postflop_action(
                    actor=actor,
                    arch=arch,
                    action=d["action"],
                    amount=d["amount"],
                    pot=d["pot_after"],
                    street=street,
                    texture=texture,
                    is_preflop_aggressor=(pfa == actor.id),
                    facing_bet=raises_before >= 1,
                )
        except Exception:
            log.exception("narration failed (non-fatal)")
            note = None

        if note is not None:
            await self._emit("coach_narration", {
                "hand_id": self.hand.hand_id,
                "kind": note.kind,
                "headline": note.headline,
                "insight": note.insight,
                "range_hint": note.range_hint,
                "by": actor.name,
                "tier": self.current_tier,
            })

    async def _narrate_board(self, ev) -> None:
        """Generate a teaching narration for a new board."""
        d = ev.data
        try:
            # d["board"] is a list of STRING cards from history; convert to ints
            board_ints = [card(c) for c in d["board"]]
            texture = analyze_texture(board_ints)
            note = narrate_board(d["street"], d["board"], texture)
        except Exception:
            log.exception("board narration failed (non-fatal)")
            return
        await self._emit("coach_narration", {
            "hand_id": self.hand.hand_id,
            "kind": note.kind,
            "headline": note.headline,
            "insight": note.insight,
            "range_hint": note.range_hint,
            "by": None,
            "tier": self.current_tier,
        })

    async def _emit_action_to_act(self, req: ActionRequest) -> None:
        assert self.hand is not None
        legal = LegalActionsView(
            can_fold=req.legal.can_fold,
            can_check=req.legal.can_check,
            can_call=req.legal.can_call,
            call_amount=req.legal.call_amount,
            can_bet=req.legal.can_bet,
            can_raise=req.legal.can_raise,
            min_raise_to=req.legal.min_raise_to,
            max_raise_to=req.legal.max_raise_to,
        )
        data = ActionToActData(
            hand_id=self.hand.hand_id,
            seq=req.seq,
            seat=req.seat,
            player_id=req.player_id,
            street=req.street.value,
            to_call=req.to_call,
            pot=req.pot,
            legal=legal,
        ).model_dump()
        await self._emit("action_to_act", data)

    async def _emit_coach_tip(self, req: ActionRequest) -> DecisionContext:
        assert self.hand is not None
        hero = next(p for p in self.table.players if not p.is_bot)
        ctx = await asyncio.to_thread(
            compute_decision_context, self.hand, req, hero, equity_samples=1200
        )
        tier_strings = render_tier(ctx, self.current_tier)
        # Map internal verdict key → which UI button to highlight
        verdict_button = _verdict_to_button(ctx.verdict)
        math = CoachMath(
            equity=ctx.equity,
            pot_odds_required=ctx.pot_odds_required,
            edge=ctx.edge,
            mdf=ctx.mdf,
            alpha=ctx.alpha,
            outs=ctx.outs,
            next_card_pct=ctx.rule_2_4[0],
            by_river_pct=ctx.rule_2_4[1],
            ev_by_action=ctx.ev_by_action,
            ev_labels=ctx.ev_labels,
            verdict=ctx.verdict,
            verdict_label=ctx.verdict_label,
            verdict_button=verdict_button,
            notes=ctx.notes,
        )
        data = CoachTipData(
            hand_id=self.hand.hand_id,
            seq=req.seq,
            tier=self.current_tier,
            math=math,
        ).model_dump()
        data["tier_strings"] = tier_strings
        data["villain_archetype"] = ctx.villain_archetype
        data["villain_range_size"] = ctx.villain_range_size
        data["spot"] = ctx.spot
        data["spot_intent"] = ctx.spot_intent
        data["has_fold_equity"] = ctx.has_fold_equity
        data["has_backup_equity"] = ctx.has_backup_equity
        # Concept focus: if a lesson is active AND this live spot exercises it,
        # surface a "try the concept here" hint.
        data["concept_focus"] = self._compute_concept_focus(ctx)
        # Step-by-step math derivation — the actual "how we got these numbers" walkthrough.
        try:
            steps = derive_math(
                hero_cards=hero.hole_cards,
                board=self.hand.board,
                pot=req.pot,
                to_call=req.to_call,
                equity=ctx.equity,
                outs=ctx.outs,
                next_card_pct=ctx.rule_2_4[0],
                by_river_pct=ctx.rule_2_4[1],
                street=req.street.value,
                legal_can_raise=req.legal.can_raise,
                bb=self.table.bb,
            )
            data["derivation"] = [
                {"label": s.label, "q": s.q, "formula": s.formula,
                 "numbers": s.numbers, "result": s.result, "gloss": s.gloss}
                for s in steps
            ]
        except Exception:
            log.exception("derivation failed (non-fatal)")
            data["derivation"] = []
        await self._emit("coach_tip", data)
        return ctx

    async def _emit_hand_end(self) -> None:
        assert self.hand is not None
        result = self.hand.result
        if result is None:
            return
        hero = next(p for p in self.table.players if not p.is_bot)
        hero_net = hero.stack - getattr(self, "_hero_stack_at_hand_start", hero.stack)
        data = HandEndData(
            hand_id=self.hand.hand_id,
            winners=result.winners,
            showdown=result.showdown,
            final_board=[to_str(c) for c in result.final_board],
            hero_net=hero_net,
            hero_stack_after=hero.stack,
        ).model_dump()
        await self._emit("hand_end", data)
