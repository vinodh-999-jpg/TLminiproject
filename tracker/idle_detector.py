"""
idle_detector.py — Detects keyboard/mouse inactivity using pynput listeners.

Runs listeners in daemon threads; no busy-wait polling.
Emits callbacks: on_idle(seconds) and on_resume()
"""
from __future__ import annotations

import threading
import time
import logging
from typing import Callable, Optional

from pynput import keyboard, mouse

log = logging.getLogger(__name__)


class IdleDetector:
    def __init__(
        self,
        threshold_seconds: int = 300,
        on_idle: Optional[Callable[[float], None]] = None,
        on_resume: Optional[Callable[[], None]] = None,
    ):
        self.threshold_seconds = threshold_seconds
        self._on_idle = on_idle or (lambda s: None)
        self._on_resume = on_resume or (lambda: None)

        self._last_event: float = time.monotonic()
        self._is_idle: bool = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

        # pynput listeners (daemon threads, near-zero CPU when idle)
        self._kb_listener = keyboard.Listener(on_press=self._activity, daemon=True)
        self._mouse_listener = mouse.Listener(
            on_move=self._activity,
            on_click=self._activity,
            on_scroll=self._activity,
            daemon=True,
        )
        # Watcher thread
        self._watcher = threading.Thread(target=self._watch, daemon=True)

    def start(self):
        self._kb_listener.start()
        self._mouse_listener.start()
        self._watcher.start()
        log.info(f"IdleDetector started (threshold={self.threshold_seconds}s)")

    def stop(self):
        self._stop_event.set()
        self._kb_listener.stop()
        self._mouse_listener.stop()

    def is_idle(self) -> bool:
        return self._is_idle

    def seconds_idle(self) -> float:
        return time.monotonic() - self._last_event

    def reset_idle(self):
        """Manually signal activity (e.g., on app switch)."""
        self._activity()

    # ── Internal ─────────────────────────────────────────────────────────────

    def _activity(self, *_):
        with self._lock:
            self._last_event = time.monotonic()
            if self._is_idle:
                self._is_idle = False
                try:
                    self._on_resume()
                except Exception as e:
                    log.debug(f"on_resume callback error: {e}")

    def _watch(self):
        """Watcher: checks idle state every second (very low overhead)."""
        while not self._stop_event.is_set():
            idle_secs = self.seconds_idle()
            if not self._is_idle and idle_secs >= self.threshold_seconds:
                with self._lock:
                    self._is_idle = True
                try:
                    self._on_idle(idle_secs)
                except Exception as e:
                    log.debug(f"on_idle callback error: {e}")
            time.sleep(1)
