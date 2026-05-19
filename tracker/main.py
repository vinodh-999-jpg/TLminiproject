"""
main.py — Entry point for the productivity tracker.

Starts:
  1. DB init
  2. Backend FastAPI server (background thread via uvicorn)
  3. IdleDetector
  4. Main poll loop
  5. System tray icon (blocking — keeps process alive)
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
import webbrowser
from pathlib import Path

import colorlog

# ── Logging setup ─────────────────────────────────────────────────────────────
def _setup_logging():
    handler = colorlog.StreamHandler()
    handler.setFormatter(colorlog.ColoredFormatter(
        "%(log_color)s%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        log_colors={
            "DEBUG":    "cyan",
            "INFO":     "green",
            "WARNING":  "yellow",
            "ERROR":    "red",
            "CRITICAL": "bold_red",
        },
    ))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    # Silence noisy libs
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
    logging.getLogger("comtypes").setLevel(logging.WARNING)

_setup_logging()
log = logging.getLogger("tracker.main")

# ── Imports (after logging) ───────────────────────────────────────────────────
from datetime import datetime, timezone
from tracker.config import cfg
from tracker.database import init_db, Activity, SessionLocal
from tracker.monitor import get_active_window
from tracker.classifier import classify
from tracker.idle_detector import IdleDetector
from tracker.battery_guard import BatteryGuard
from tracker.session_manager import SessionManager
import tracker.state as state


def _save_activity(win, category: str):
    """Persist one Activity snapshot to the DB (best-effort)."""
    try:
        db = SessionLocal()
        db.add(Activity(
            timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
            app_name=win.app_name,
            process=win.process,
            window_title=win.window_title,
            url=win.url,
            domain=win.domain,
            category=category,
        ))
        db.commit()
    except Exception as e:
        log.debug(f"Activity save error: {e}")
    finally:
        try:
            db.close()
        except Exception:
            pass


# ── Backend server ────────────────────────────────────────────────────────────
def _start_backend():
    """Run FastAPI backend in a daemon thread."""
    try:
        import uvicorn
        from backend.main import app
        uvicorn.run(
            app,
            host=cfg.BACKEND_HOST,
            port=cfg.BACKEND_PORT,
            log_level="warning",
        )
    except Exception as e:
        log.error(f"Backend failed to start: {e}")


# ── Tray icon ─────────────────────────────────────────────────────────────────
def _build_tray(session_mgr: SessionManager, stop_event: threading.Event):
    """Build and return a pystray Icon. Returns None if pystray unavailable."""
    try:
        import pystray
        from PIL import Image, ImageDraw

        # Generate a simple colored circle icon
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([4, 4, size - 4, size - 4], fill=(108, 99, 255, 255))
        draw.ellipse([16, 16, size - 16, size - 16], fill=(255, 255, 255, 200))

        def open_dashboard(icon, item):
            webbrowser.open(f"http://{cfg.BACKEND_HOST}:{cfg.BACKEND_PORT}")

        def view_pending(icon, item):
            webbrowser.open(f"http://{cfg.BACKEND_HOST}:{cfg.BACKEND_PORT}/#pending")

        def quit_app(icon, item):
            log.info("Quit requested from tray")
            stop_event.set()
            icon.stop()

        def get_title(icon=None):
            cat = session_mgr.current_category or "Idle"
            dur = session_mgr.current_duration_sec
            pending = session_mgr.pending_count()
            mins = dur // 60
            secs = dur % 60
            return f"Tracker — {cat} ({mins}m {secs}s) | {pending} pending"

        menu = pystray.Menu(
            pystray.MenuItem("📊 Open Dashboard", open_dashboard, default=True),
            pystray.MenuItem("📋 Review & Log to Clockify", view_pending),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(get_title, lambda icon, item: None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("❌ Quit", quit_app),
        )
        icon = pystray.Icon("ProductivityTracker", img, "Productivity Tracker", menu)
        return icon
    except Exception as e:
        log.warning(f"System tray unavailable: {e}")
        return None


# ── Main poll loop ────────────────────────────────────────────────────────────
def _poll_loop(
    session_mgr: SessionManager,
    idle_detector: IdleDetector,
    battery_guard: BatteryGuard,
    stop_event: threading.Event,
):
    log.info("Poll loop started")
    while not stop_event.is_set():
        try:
            poll_interval, url_enabled = battery_guard.get_settings()
            is_idle = idle_detector.is_idle()

            if not is_idle:
                win = get_active_window()
                if win:
                    # Suppress URL scraping on low battery
                    url = win.url if url_enabled else None
                    domain = win.domain if url_enabled else None
                    win.url = url
                    win.domain = domain
                    category = classify(win.process, win.window_title, domain, url)
                    _save_activity(win, category)
                    session_mgr.process_snapshot(
                        category=category,
                        app_name=win.app_name,
                        url=url,
                        window_title=win.window_title,
                        process=win.process,
                        is_idle=False,
                    )
                    log.debug(f"[{category}] {win.app_name} — {win.window_title[:50]}")
                else:
                    session_mgr.process_snapshot(
                        category="Unknown",
                        app_name="",
                        is_idle=False,
                    )
            else:
                session_mgr.process_snapshot(
                    category="",
                    app_name="",
                    is_idle=True,
                )

        except Exception as e:
            log.debug(f"Poll error: {e}")

        stop_event.wait(timeout=poll_interval)

    log.info("Poll loop stopped")


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("  Productivity Tracker starting…")
    log.info("=" * 60)

    # Init DB
    init_db()
    log.info(f"Database ready at: {cfg.DATABASE_PATH}")

    stop_event = threading.Event()

    # Start backend
    backend_thread = threading.Thread(target=_start_backend, daemon=True, name="backend")
    backend_thread.start()
    log.info(f"Backend starting on http://{cfg.BACKEND_HOST}:{cfg.BACKEND_PORT}")
    time.sleep(1.5)  # Give uvicorn a moment to bind

    # Core components
    session_mgr = SessionManager(
        min_session_seconds=cfg.MIN_SESSION_SECONDS,
        merge_window_seconds=cfg.MERGE_WINDOW_SECONDS,
    )
    state.session_mgr = session_mgr  # expose to backend SSE thread
    idle_detector = IdleDetector(
        threshold_seconds=cfg.IDLE_THRESHOLD_SECONDS,
        on_idle=lambda s: log.info(f"😴 Idle for {s:.0f}s"),
        on_resume=lambda: log.info("⚡ Activity resumed"),
    )
    battery_guard = BatteryGuard(low_threshold=cfg.BATTERY_LOW_THRESHOLD)

    idle_detector.start()

    # Poll loop thread
    poll_thread = threading.Thread(
        target=_poll_loop,
        args=(session_mgr, idle_detector, battery_guard, stop_event),
        daemon=True,
        name="poll-loop",
    )
    poll_thread.start()

    # Graceful shutdown on Ctrl+C
    def _shutdown(sig=None, frame=None):
        log.info("Shutting down…")
        stop_event.set()
        session_mgr.force_end()
        idle_detector.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Open dashboard in browser
    time.sleep(0.5)
    webbrowser.open(f"http://{cfg.BACKEND_HOST}:{cfg.BACKEND_PORT}")

    # Try system tray (blocking); fall back to simple wait
    tray = _build_tray(session_mgr, stop_event)
    if tray:
        log.info("System tray icon active. Right-click tray to open dashboard or quit.")
        try:
            tray.run()
        except Exception as e:
            log.warning(f"Tray run error: {e}")
    else:
        log.info("Running without system tray. Press Ctrl+C to stop.")
        stop_event.wait()

    _shutdown()


if __name__ == "__main__":
    main()
