"""
activities.py — Recent raw activity log endpoint.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from tracker.database import Activity, get_db

router = APIRouter(prefix="/activities", tags=["activities"])


class ActivityOut(BaseModel):
    id: int
    timestamp: datetime
    app_name: str
    process: str
    window_title: str
    url: Optional[str]
    domain: Optional[str]
    category: str

    class Config:
        from_attributes = True


@router.get("/", response_model=List[ActivityOut])
def list_activities(limit: int = 50, db: Session = Depends(get_db)):
    rows = (
        db.query(Activity)
        .order_by(Activity.timestamp.desc())
        .limit(limit)
        .all()
    )
    return rows


@router.get("/current")
def current_activity(db: Session = Depends(get_db)):
    """Latest activity snapshot (for live status widget)."""
    row = db.query(Activity).order_by(Activity.timestamp.desc()).first()
    if not row:
        return {"app_name": "—", "category": "Unknown", "window_title": ""}
    return ActivityOut.from_orm(row)
