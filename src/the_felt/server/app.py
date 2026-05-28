"""FastAPI app entrypoint."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from the_felt.agents.hand_strength import _populate_table as _populate_preflop_table
from the_felt.server.api_lessons import router as lessons_router
from the_felt.server.ws import ws_endpoint

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="the_felt", version="0.1.0")


class NoCacheMiddleware(BaseHTTPMiddleware):
    """Disable caching for static assets during development."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.endswith((".js", ".css", ".html", "/")):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response


app.add_middleware(NoCacheMiddleware)

app.include_router(lessons_router)


@app.on_event("startup")
async def _warm_caches() -> None:
    """Precompute the preflop equity-vs-random table so the first hand isn't slow."""
    import asyncio
    logging.getLogger(__name__).info("warming preflop equity table…")
    await asyncio.to_thread(_populate_preflop_table)
    logging.getLogger(__name__).info("preflop equity table ready")


@app.get("/api/config")
async def public_config():
    """Public flags the client uses to toggle recruiter-mode features."""
    import os
    return {
        "public_demo": os.environ.get("THE_FELT_PUBLIC_DEMO", "").strip() in ("1", "true", "yes"),
        "version": "0.2.0",
    }


@app.websocket("/ws")
async def websocket_route(websocket: WebSocket) -> None:
    await ws_endpoint(websocket)


# Static files served from src/the_felt/static
_static_dir = Path(__file__).resolve().parent.parent / "static"
app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")
