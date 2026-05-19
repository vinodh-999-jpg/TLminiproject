"""
settings.py — Settings CRUD (Clockify keys, thresholds, etc.)
"""
from __future__ import annotations

from typing import Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from tracker.database import Setting, get_db

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsPayload(BaseModel):
    settings: Dict[str, str]


@router.get("/")
def get_settings(db: Session = Depends(get_db)):
    rows = db.query(Setting).all()
    return {r.key: r.value for r in rows}


@router.post("/")
def update_settings(body: SettingsPayload, db: Session = Depends(get_db)):
    for key, val in body.settings.items():
        row = db.get(Setting, key)
        if row:
            row.value = val
        else:
            db.add(Setting(key=key, value=val))
    db.commit()
    return {"ok": True, "updated": list(body.settings.keys())}
