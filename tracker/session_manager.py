"""
session_manager.py — Tracks work sessions and writes them to SQLite as PENDING.

⚠️ Clockify is NEVER called here. All sessions await user review.

State machine per session:
  - start_session(category, ...)
  - append_activity(...)
  - end_session()  → writes Session_ record with status=PENDING
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from tracker.database import SessionLocal, Activity, Session_
from tracker.classifier import classify

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ActiveSession:
    """In-memory representation of an ongoing session."""

    # Browser process suffixes to strip from window titles
    _BROWSER_SUFFIXES = [
        " - Google Chrome", " - Microsoft Edge", " - Firefox",
        " - Brave", " - Opera", " - Vivaldi",
        " – Google Chrome", " – Microsoft Edge",  # en-dash variant
    ]
    # Non-browser apps where window title is also useful
    _APP_TITLE_APPS = {"slack", "discord", "teams", "zoom", "notion", "obsidian"}

    def __init__(self, category: str, start_time: datetime):
        self.category = category
        self.start_time = start_time
        self.current_app: str = ""       # most recent app seen
        self.app_names: list[str] = []
        self.urls: list[str] = []
        self.page_titles: list[str] = [] # cleaned page titles (max 5 unique)
        self._lock = threading.Lock()

    @staticmethod
    def _clean_title(window_title: str, process: str) -> str:
        """Strip browser/app suffixes to get the actual page/window title."""
        import re
        t = window_title

        # Strip browser suffix:  "Page Title - Google Chrome"  →  "Page Title"
        for suffix in ActiveSession._BROWSER_SUFFIXES:
            if t.endswith(suffix):
                t = t[: -len(suffix)].strip()
                break

        # Strip Edge/Chrome multi-tab noise: "and 3 more pages - Personal"
        t = re.sub(r"\s+and \d+ more pages?.*$", "", t, flags=re.IGNORECASE).strip()
        # Strip profile suffix: "- Personal", "- Work", "- Default"
        t = re.sub(r"\s*-\s*(Personal|Work|Default|Profile \d+)\s*$", "", t, flags=re.IGNORECASE).strip()

        # Strip trailing " - AppName" for non-browser apps
        proc_name = process.lower().replace(".exe", "")
        t = re.sub(r"\s*[-–]\s*" + re.escape(proc_name) + r"$", "", t, flags=re.IGNORECASE).strip()

        # Truncate overly long titles
        if len(t) > 60:
            t = t[:57] + "…"

        return t

    def add_snapshot(self, app_name: str, url: Optional[str],
                     window_title: str = "", process: str = ""):
        with self._lock:
            if app_name:
                self.current_app = app_name
                if app_name not in self.app_names:
                    self.app_names.append(app_name)
            if url and url not in self.urls:
                self.urls.append(url)
            # Extract and store meaningful page title (replace last to keep most recent)
            if window_title:
                cleaned = self._clean_title(window_title, process)
                if cleaned and len(cleaned) > 3:
                    # Keep unique titles but cap at 5
                    if cleaned not in self.page_titles:
                        if len(self.page_titles) >= 5:
                            self.page_titles.pop(0)  # drop oldest
                        self.page_titles.append(cleaned)

    @property
    def duration_sec(self) -> int:
        return max(0, int((_utcnow() - self.start_time).total_seconds()))

    def description(self) -> str:
        """Build a clean, concise description from page titles."""
        if self.page_titles:
            # Show last 2 unique titles (most recent activity)
            recent = self.page_titles[-2:]
            return "; ".join(recent)

        # Fall back to domains
        if self.urls:
            from urllib.parse import urlparse
            domains = list({urlparse(u).netloc.replace("www.", "")
                           for u in self.urls[:3] if u})
            if domains:
                return ", ".join(domains)

        # Final fallback
        return ", ".join(self.app_names[:2]) if self.app_names else self.category


class SessionManager:
    def __init__(
        self,
        min_session_seconds: int = 30,
        merge_window_seconds: int = 120,
    ):
        self.min_session_seconds = min_session_seconds
        self.merge_window_seconds = merge_window_seconds

        self._current: Optional[ActiveSession] = None
        self._lock = threading.Lock()

        # Track last finalized session for merge logic
        self._last_category: Optional[str] = None
        self._last_end_time: Optional[datetime] = None
        self._last_session_id: Optional[int] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def process_snapshot(
        self,
        category: str,
        app_name: str,
        url: Optional[str] = None,
        window_title: str = "",
        process: str = "",
        is_idle: bool = False,
    ):
        """
        Called every poll tick. Handles session start/continue/end.
        """
        with self._lock:
            if is_idle:
                if self._current:
                    log.debug("Idle detected — pausing session")
                    self._finalize_current()
                return

            if self._current is None:
                # Check if we should merge with last session
                if self._should_merge(category):
                    self._reopen_last_session(category)
                else:
                    self._start_new(category)

            elif self._current.category != category:
                # Category changed — finalize old, start new
                self._finalize_current()
                if self._should_merge(category):
                    self._reopen_last_session(category)
                else:
                    self._start_new(category)

            # Add this snapshot's data (including page title)
            if self._current:
                self._current.add_snapshot(app_name, url,
                                           window_title=window_title,
                                           process=process)

    def force_end(self):
        """Call on application shutdown."""
        with self._lock:
            if self._current:
                self._finalize_current()

    @property
    def current_category(self) -> Optional[str]:
        return self._current.category if self._current else None

    @property
    def current_app(self) -> str:
        return self._current.current_app if self._current else ""

    @property
    def current_duration_sec(self) -> int:
        return self._current.duration_sec if self._current else 0

    @property
    def current_session_start_iso(self) -> Optional[str]:
        """ISO string of when the current session started (UTC naive, no Z suffix)."""
        return self._current.start_time.isoformat() if self._current else None

    @property
    def current_apps_seen(self) -> list[str]:
        return list(self._current.app_names) if self._current else []

    @property
    def current_description(self) -> str:
        """Live session description built from page titles seen so far."""
        return self._current.description() if self._current else ""

    @property
    def current_page_titles(self) -> list[str]:
        return list(self._current.page_titles) if self._current else []

    # ── Internal ──────────────────────────────────────────────────────────────

    def _start_new(self, category: str):
        self._current = ActiveSession(category=category, start_time=_utcnow())
        log.info(f"▶ Session started: {category}")

    def _should_merge(self, category: str) -> bool:
        if not self._last_session_id:
            return False
        if self._last_category != category:
            return False
        if not self._last_end_time:
            return False
        gap = (_utcnow() - self._last_end_time).total_seconds()
        return gap <= self.merge_window_seconds

    def _reopen_last_session(self, category: str):
        """Delete the last PENDING session from DB and continue its timeline."""
        db = SessionLocal()
        try:
            old = db.get(Session_, self._last_session_id)
            if old and old.status == "PENDING":
                # Reuse start time from old session
                start = old.start_time
                db.delete(old)
                db.commit()
                log.info(f"🔀 Merging session: {category}")
                self._current = ActiveSession(category=category, start_time=start)
                return
        except Exception as e:
            log.debug(f"Merge reopen error: {e}")
        finally:
            db.close()
        self._start_new(category)

    def _finalize_current(self):
        sess = self._current
        self._current = None

        if sess is None:
            return
        if sess.duration_sec < self.min_session_seconds:
            log.debug(f"Session too short ({sess.duration_sec}s) — discarded")
            return

        end_time = _utcnow()
        self._last_category = sess.category
        self._last_end_time = end_time

        db = SessionLocal()
        try:
            record = Session_(
                category=sess.category,
                description=sess.description(),
                start_time=sess.start_time,
                end_time=end_time,
                duration_sec=sess.duration_sec,
                app_names=json.dumps(sess.app_names),
                urls=json.dumps(sess.urls),
                status="PENDING",
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            self._last_session_id = record.id
            log.info(f"✅ Session saved (PENDING): {sess.category} — {sess.duration_sec}s")
        except Exception as e:
            log.error(f"Failed to save session: {e}")
            db.rollback()
        finally:
            db.close()

    def pending_count(self) -> int:
        db = SessionLocal()
        try:
            return db.query(Session_).filter(Session_.status == "PENDING").count()
        finally:
            db.close()
