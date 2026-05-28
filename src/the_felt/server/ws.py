"""WebSocket endpoint — bridges client messages to the Session loop."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid

from fastapi import WebSocket, WebSocketDisconnect

from the_felt.persistence.store import get_store
from the_felt.server.protocol import JoinData
from the_felt.server.session import Session

log = logging.getLogger(__name__)


async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    session: Session | None = None
    session_task: asyncio.Task | None = None
    sender_task: asyncio.Task | None = None

    try:
        first = await websocket.receive_json()
        if first.get("type") != "join":
            await websocket.send_json({"type": "error", "v": 1, "data": {"code": "expected_join", "message": "First message must be join"}})
            await websocket.close()
            return

        join = JoinData(**first.get("data", {}))
        # Public-demo mode (recruiter visits on magech.ai): every visitor gets an
        # ephemeral user id and the persistence store is bypassed so the DB
        # stays clean and one visitor's blunders don't ratchet down the bot
        # difficulty for the next.
        is_public_demo = os.environ.get("THE_FELT_PUBLIC_DEMO", "").strip() in ("1", "true", "yes")
        if is_public_demo:
            user_id = f"demo_{uuid.uuid4().hex[:10]}"
            store = None
        else:
            store = await get_store()
            user_id = f"u_{join.user_name.lower().replace(' ', '_')}"
        session = Session(
            user_id=user_id,
            user_name=join.user_name,
            seats=max(2, min(10, join.seats)),
            sb=join.sb,
            bb=join.bb,
            stack_bb=join.stack_bb,
            store=store,
        )

        async def sender() -> None:
            while not session.closed.is_set():
                try:
                    msg = await asyncio.wait_for(session.outbound.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                await websocket.send_json(msg)

        sender_task = asyncio.create_task(sender())
        session_task = asyncio.create_task(session.run())

        await websocket.send_json({
            "type": "joined",
            "v": 1,
            "data": {"user_id": session.user_id, "seats": session.seats},
        })

        while True:
            msg = await websocket.receive_json()
            mtype = msg.get("type")
            data = msg.get("data", {})

            if mtype == "action":
                await session.user_action.put(data)
            elif mtype == "next_hand":
                session.next_hand_signal.set()
            elif mtype == "ask_coach":
                await session.ask_coach_q.put(data)
            elif mtype == "start_drill":
                await session.drill_q.put({"kind": "start_drill", **data})
            elif mtype == "submit_drill_answer":
                await session.drill_q.put({"kind": "submit_drill_answer", **data})
            elif mtype == "set_active_lesson":
                await session.drill_q.put({"kind": "set_active_lesson", **data})
            elif mtype == "submit_quiz_answer":
                # answers: list[Any | "__revealed__" | null]
                await session._quiz_answers.put(data)
            elif mtype == "graduate_stage":
                # Advance the user's training stage
                from the_felt.curriculum.stages import next_stage
                nxt = next_stage(session.stage_state.stage_id)
                if nxt is not None:
                    session.stage_state.stage_id = nxt.id
                    session.stage_state.correct_streak = 0
                    session.stage_state.clean_hands = 0
                    session.stage_state.quiz_count = 0
                    session.stage_state.correct_count = 0
                    await websocket.send_json({
                        "type": "stage_change",
                        "v": 1,
                        "data": {
                            "stage_id": nxt.id,
                            "stage_title": nxt.title,
                            "stage_teaches": nxt.teaches,
                            "stage_intro": nxt.intro,
                        },
                    })
            elif mtype == "set_tier":
                # User-facing override; allows forcing a tier
                t = data.get("tier")
                if t is None:
                    session.user_tier_override = None
                else:
                    session.user_tier_override = max(1, min(4, int(t)))
                session._update_tier()
                await websocket.send_json({"type": "tier_update", "v": 1, "data": {"tier": session.current_tier}})
            elif mtype == "ping":
                await websocket.send_json({"type": "pong", "v": 1, "data": {}})
            else:
                await websocket.send_json({"type": "error", "v": 1, "data": {"code": "unknown_type", "message": mtype}})

    except WebSocketDisconnect:
        log.info("client disconnected")
    except Exception:
        log.exception("ws_endpoint crashed")
    finally:
        if session is not None:
            session.closed.set()
        for task in (session_task, sender_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
