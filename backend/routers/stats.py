"""
stats.py — Aggregated productivity stats for the dashboard charts.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from tracker.database import Session_, get_db
from tracker.classifier import compute_focus_score, CATEGORY_COLORS

router = APIRouter(prefix="/stats", tags=["stats"])


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _local_today_start_as_utc() -> datetime:
    """Return local midnight expressed as a naive UTC datetime for DB comparisons.
    Sessions are stored as naive UTC, so we need the UTC equivalent of local 00:00.
    e.g. IST midnight (00:00+05:30) = 18:30 UTC the previous day.
    """
    now_local = datetime.now()   # local wall clock, no tzinfo
    now_utc   = _utcnow()        # UTC wall clock, no tzinfo
    # offset is negative for zones east of UTC (IST = UTC+5:30 → offset ≈ -5h30m)
    offset = now_utc - now_local
    local_midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight + offset  # shift local midnight into UTC


@router.get("/today")
def stats_today(db: Session = Depends(get_db)):
    today_start = _local_today_start_as_utc()
    sessions = (
        db.query(Session_)
        .filter(Session_.start_time >= today_start)
        .filter(Session_.status.in_(["PENDING", "CONFIRMED", "LOGGED"]))
        .all()
    )
    return _aggregate(sessions)


@router.get("/week")
def stats_week(db: Session = Depends(get_db)):
    week_start = _utcnow() - timedelta(days=7)
    sessions = (
        db.query(Session_)
        .filter(Session_.start_time >= week_start)
        .filter(Session_.status.in_(["PENDING", "CONFIRMED", "LOGGED"]))
        .all()
    )
    return _aggregate(sessions)


@router.get("/timeline")
def stats_timeline(days: int = 7, db: Session = Depends(get_db)):
    """Returns per-day category breakdown for the last N days."""
    start = _utcnow() - timedelta(days=days)
    sessions = (
        db.query(Session_)
        .filter(Session_.start_time >= start)
        .filter(Session_.status.in_(["PENDING", "CONFIRMED", "LOGGED"]))
        .order_by(Session_.start_time.asc())
        .all()
    )

    # Build day → category → seconds map
    day_map: dict = {}
    for s in sessions:
        day = s.start_time.strftime("%Y-%m-%d")
        day_map.setdefault(day, {})
        day_map[day][s.category] = day_map[day].get(s.category, 0) + s.duration_sec

    # Fill all days in range
    result = []
    for i in range(days, -1, -1):
        d = (_utcnow() - timedelta(days=i)).strftime("%Y-%m-%d")
        result.append({"date": d, "categories": day_map.get(d, {})})
    return result


def _aggregate(sessions: list) -> dict:
    by_cat: dict = {}
    for s in sessions:
        by_cat[s.category] = by_cat.get(s.category, 0) + s.duration_sec

    total = sum(by_cat.values())
    session_dicts = [{"category": s.category, "duration_sec": s.duration_sec} for s in sessions]

    return {
        "total_sec": total,
        "focus_score": compute_focus_score(session_dicts),
        "session_count": len(sessions),
        "by_category": [
            {
                "category": cat,
                "duration_sec": secs,
                "pct": round(secs / total * 100, 1) if total else 0,
                "color": CATEGORY_COLORS.get(cat, "#999"),
            }
            for cat, secs in sorted(by_cat.items(), key=lambda x: -x[1])
        ],
    }
