"""
database.py — SQLAlchemy ORM models + DB session setup (shared by tracker and backend).
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer,
    String, Text, create_engine, event,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# ── Locate DB path ────────────────────────────────────────────────────────────
_base = Path(__file__).parent.parent
_db_path = Path(os.getenv("DATABASE_PATH", str(_base / "data" / "tracker.db")))
_db_path.parent.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite:///{_db_path}"
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

# Enable WAL mode for better concurrent read/write performance
@event.listens_for(engine, "connect")
def set_wal_mode(dbapi_conn, _):
    dbapi_conn.execute("PRAGMA journal_mode=WAL")
    dbapi_conn.execute("PRAGMA synchronous=NORMAL")


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Session:
    """FastAPI dependency: yields a DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── ORM Models ────────────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


class Activity(Base):
    """Raw activity snapshot — one row per poll tick (every N seconds)."""
    __tablename__ = "activities"

    id          = Column(Integer, primary_key=True, index=True)
    timestamp   = Column(DateTime, default=datetime.utcnow, index=True)
    app_name    = Column(String(256))
    process     = Column(String(128))
    window_title = Column(Text)
    url         = Column(Text, nullable=True)
    domain      = Column(String(256), nullable=True)
    category    = Column(String(64))


class Session_(Base):
    """
    Aggregated work session.
    Status lifecycle:  PENDING → CONFIRMED → LOGGED
                                           → SKIPPED
    """
    __tablename__ = "sessions"

    id              = Column(Integer, primary_key=True, index=True)
    category        = Column(String(64), index=True)
    description     = Column(Text, nullable=True)
    project_id      = Column(String(128), nullable=True)   # Clockify project ID
    project_name    = Column(String(256), nullable=True)
    start_time      = Column(DateTime, index=True)
    end_time        = Column(DateTime, nullable=True)
    duration_sec    = Column(Integer, default=0)
    app_names       = Column(Text, nullable=True)          # JSON list of apps in session
    urls            = Column(Text, nullable=True)          # JSON list of URLs
    clockify_entry_id = Column(String(128), nullable=True)
    status          = Column(String(16), default="PENDING", index=True)
    # PENDING | CONFIRMED | LOGGED | SKIPPED
    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Setting(Base):
    """Key-value store for runtime settings (editable via UI)."""
    __tablename__ = "settings"

    key   = Column(String(128), primary_key=True)
    value = Column(Text)


def init_db():
    """Create all tables if they don't exist."""
    Base.metadata.create_all(bind=engine)
    # Seed default settings
    db = SessionLocal()
    defaults = {
        "idle_threshold_minutes": "5",
        "min_session_seconds": "30",
        "merge_window_minutes": "2",
        "poll_interval_seconds": "2",
        "clockify_api_key": "",
        "clockify_workspace_id": "",
        "clockify_default_project_id": "",
        "daily_reminder_time": "18:00",
        "auto_start_enabled": "false",
    }
    for k, v in defaults.items():
        if not db.get(Setting, k):
            db.add(Setting(key=k, value=v))
    db.commit()
    db.close()


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at: {_db_path}")
