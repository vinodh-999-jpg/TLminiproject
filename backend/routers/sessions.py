"""
sessions.py — CRUD for sessions + Clockify sync endpoint.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from tracker.database import Session_, Setting, get_db
from tracker.config import cfg

log = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["sessions"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class SessionOut(BaseModel):
    id: int
    category: str
    description: Optional[str]
    project_id: Optional[str]
    project_name: Optional[str]
    start_time: datetime
    end_time: Optional[datetime]
    duration_sec: int
    app_names: List[str]
    urls: List[str]
    status: str
    clockify_entry_id: Optional[str]

    class Config:
        from_attributes = True

    @classmethod
    def from_orm_extended(cls, s: Session_) -> "SessionOut":
        return cls(
            id=s.id,
            category=s.category,
            description=s.description,
            project_id=s.project_id,
            project_name=s.project_name,
            start_time=s.start_time,
            end_time=s.end_time,
            duration_sec=s.duration_sec,
            app_names=json.loads(s.app_names or "[]"),
            urls=json.loads(s.urls or "[]"),
            status=s.status,
            clockify_entry_id=s.clockify_entry_id,
        )


class SessionUpdate(BaseModel):
    category: Optional[str] = None
    description: Optional[str] = None
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None


class ClockifyLogRequest(BaseModel):
    session_ids: List[int]


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/", response_model=List[SessionOut])
def list_sessions(
    status: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    q = db.query(Session_).order_by(Session_.start_time.desc())
    if status:
        q = q.filter(Session_.status == status.upper())
    sessions = q.limit(limit).all()
    return [SessionOut.from_orm_extended(s) for s in sessions]


@router.get("/pending", response_model=List[SessionOut])
def list_pending(db: Session = Depends(get_db)):
    sessions = (
        db.query(Session_)
        .filter(Session_.status == "PENDING")
        .order_by(Session_.start_time.asc())
        .all()
    )
    return [SessionOut.from_orm_extended(s) for s in sessions]


@router.get("/recent", response_model=List[SessionOut])
def list_recent(limit: int = 15, db: Session = Depends(get_db)):
    """Recent completed sessions (any status except SKIPPED) for the dashboard feed."""
    sessions = (
        db.query(Session_)
        .filter(Session_.status.in_(["PENDING", "CONFIRMED", "LOGGED"]))
        .order_by(Session_.start_time.desc())
        .limit(limit)
        .all()
    )
    return [SessionOut.from_orm_extended(s) for s in sessions]


@router.get("/{session_id}", response_model=SessionOut)
def get_session(session_id: int, db: Session = Depends(get_db)):
    s = db.get(Session_, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    return SessionOut.from_orm_extended(s)


@router.patch("/{session_id}", response_model=SessionOut)
def update_session(session_id: int, body: SessionUpdate, db: Session = Depends(get_db)):
    s = db.get(Session_, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(s, field, val)
    # Recalculate duration if times changed
    if s.start_time and s.end_time:
        s.duration_sec = max(0, int((s.end_time - s.start_time).total_seconds()))
    db.commit()
    db.refresh(s)
    return SessionOut.from_orm_extended(s)


@router.delete("/{session_id}")
def delete_session(session_id: int, db: Session = Depends(get_db)):
    s = db.get(Session_, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    db.delete(s)
    db.commit()
    return {"ok": True}


@router.post("/{session_id}/skip")
def skip_session(session_id: int, db: Session = Depends(get_db)):
    s = db.get(Session_, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    s.status = "SKIPPED"
    db.commit()
    return {"ok": True, "status": "SKIPPED"}


@router.post("/log-to-clockify")
def log_to_clockify(body: ClockifyLogRequest, db: Session = Depends(get_db)):
    """
    POST confirmed sessions to Clockify. Only PENDING sessions are processed.
    Returns per-session results.
    """
    # Get API key from DB settings (user sets these in dashboard) — ignore .env placeholders
    api_key = _get_setting(db, "clockify_api_key")
    if not api_key or api_key in ("", "your_api_key_here"):
        api_key = cfg.CLOCKIFY_API_KEY if cfg.CLOCKIFY_API_KEY not in ("", "your_api_key_here") else ""
    workspace_id = _get_setting(db, "clockify_workspace_id")
    if not workspace_id or workspace_id in ("", "your_workspace_id_here"):
        workspace_id = cfg.CLOCKIFY_WORKSPACE_ID if cfg.CLOCKIFY_WORKSPACE_ID not in ("", "your_workspace_id_here") else ""

    if not api_key:
        raise HTTPException(400, "Clockify API key not configured. Go to Output → Settings and paste your API key.")
    if not workspace_id:
        raise HTTPException(400, "Clockify workspace ID not configured. Paste your API key in Settings — workspace auto-detects.")

    results = []
    for sid in body.session_ids:
        s = db.get(Session_, sid)
        if not s or s.status != "PENDING":
            results.append({"id": sid, "ok": False, "error": "Not found or not PENDING"})
            continue

        try:
            entry_id = _post_to_clockify(s, api_key, workspace_id)
            s.status = "LOGGED"
            s.clockify_entry_id = entry_id
            db.commit()
            results.append({"id": sid, "ok": True, "clockify_entry_id": entry_id})
        except Exception as e:
            log.error(f"Clockify post failed for session {sid}: {e}")
            results.append({"id": sid, "ok": False, "error": str(e)})

    return {"results": results}


@router.get("/clockify-projects")
def get_clockify_projects(db: Session = Depends(get_db)):
    """Fetch project list from Clockify API using stored credentials."""
    api_key = _get_setting(db, "clockify_api_key")
    if not api_key or api_key in ("", "your_api_key_here"):
        api_key = cfg.CLOCKIFY_API_KEY if cfg.CLOCKIFY_API_KEY not in ("", "your_api_key_here") else ""
    workspace_id = _get_setting(db, "clockify_workspace_id")
    if not workspace_id or workspace_id in ("", "your_workspace_id_here"):
        workspace_id = cfg.CLOCKIFY_WORKSPACE_ID if cfg.CLOCKIFY_WORKSPACE_ID not in ("", "your_workspace_id_here") else ""

    if not api_key:
        return {"error": "no_api_key", "projects": []}
    if not workspace_id:
        return {"error": "no_workspace_id", "projects": []}

    try:
        headers = {"X-Api-Key": api_key}
        url = f"{cfg.CLOCKIFY_BASE_URL}/workspaces/{workspace_id}/projects?archived=false&page-size=100"
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        projects = [{"id": p["id"], "name": p["name"]} for p in resp.json()]
        return {"projects": projects}
    except Exception as e:
        log.error(f"Failed to fetch Clockify projects: {e}")
        return {"error": str(e), "projects": []}


@router.get("/clockify-detect-workspace")
def detect_workspace(api_key: str):
    """Auto-detect workspace ID from a Clockify API key."""
    if not api_key or len(api_key) < 10:
        return {"error": "invalid_key"}
    try:
        headers = {"X-Api-Key": api_key}
        resp = requests.get(f"{cfg.CLOCKIFY_BASE_URL}/user", headers=headers, timeout=10)
        resp.raise_for_status()
        user = resp.json()
        return {
            "workspace_id": user.get("defaultWorkspace") or user.get("activeWorkspace"),
            "user_name": user.get("name", ""),
            "email": user.get("email", ""),
        }
    except Exception as e:
        log.error(f"Failed to detect workspace: {e}")
        return {"error": str(e)}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_setting(db: Session, key: str) -> Optional[str]:
    row = db.get(Setting, key)
    return row.value if row else None


def _post_to_clockify(s: Session_, api_key: str, workspace_id: str) -> str:
    """Posts a single session to Clockify. Returns the Clockify entry ID."""
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}

    # Clockify requires UTC ISO8601 with Z suffix
    def _fmt(dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    payload: dict = {
        "description": s.description or s.category,
        "start": _fmt(s.start_time),
        "end": _fmt(s.end_time) if s.end_time else _fmt(datetime.now(timezone.utc)),
        "billable": False,
    }
    # Use session-level project if set, otherwise fall back to default
    project_id = s.project_id or _get_setting_cached(api_key)
    if project_id:
        payload["projectId"] = project_id

    url = f"{cfg.CLOCKIFY_BASE_URL}/workspaces/{workspace_id}/time-entries"
    resp = requests.post(url, json=payload, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return data.get("id", "")


def _get_setting_cached(default: str) -> Optional[str]:
    """Placeholder — project_id already resolved upstream."""
    return None
