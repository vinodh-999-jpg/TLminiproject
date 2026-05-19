"""
backend/main.py — FastAPI application.

Serves:
  - REST API  → /api/...
  - Dashboard → GET /  (serves frontend/index.html)
  - SSE live  → /api/live  (real-time status updates)
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.routers import sessions, activities, settings, stats

log = logging.getLogger(__name__)

app = FastAPI(
    title="Productivity Tracker",
    description="Background activity tracker with Clockify integration",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(sessions.router, prefix="/api")
app.include_router(activities.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(stats.router, prefix="/api")

# ── Static files (frontend) ───────────────────────────────────────────────────
_frontend_dir = Path(__file__).parent.parent / "frontend"


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_dashboard():
    index = _frontend_dir / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return HTMLResponse("<h1>Dashboard not found. Run the tracker to generate it.</h1>", status_code=404)


# Serve any other static files in frontend/
if _frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_frontend_dir)), name="static")


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


# ── SSE live status ───────────────────────────────────────────────────────────
from fastapi.responses import StreamingResponse
from tracker.database import Session_, SessionLocal, Activity
import tracker.state as state


@app.get("/api/live", include_in_schema=False)
async def live_status():
    """Server-Sent Events stream for real-time dashboard updates."""

    async def _event_generator():
        while True:
            try:
                db = SessionLocal()
                try:
                    act = db.query(Activity).order_by(Activity.timestamp.desc()).first()
                    pending_count = db.query(Session_).filter(Session_.status == "PENDING").count()
                finally:
                    db.close()

                # Pull live session state from in-memory SessionManager
                mgr = state.session_mgr
                data = {
                    "app":      act.app_name if act else "—",
                    "category": act.category if act else "Unknown",
                    "title":    act.window_title[:60] if act and act.window_title else "",
                    "pending":  pending_count,
                    # Live in-memory session (not yet written to DB)
                    "live_category":    mgr.current_category if mgr else None,
                    "live_app":         mgr.current_app if mgr else "",
                    "live_apps_seen":   mgr.current_apps_seen if mgr else [],
                    "live_duration_sec": mgr.current_duration_sec if mgr else 0,
                    "live_start_iso":   mgr.current_session_start_iso if mgr else None,
                }
                yield f"data: {json.dumps(data)}\n\n"
            except Exception as e:
                log.debug(f"SSE error: {e}")
                yield "data: {}\n\n"

            await asyncio.sleep(3)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
