"""
tracker/state.py — Shared mutable state between tracker and backend threads.

Both run in the same Python process, so a module-level variable works as a
cross-thread singleton without any IPC overhead.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tracker.session_manager import SessionManager

# Set by tracker/main.py once the SessionManager is created.
# Read by backend SSE to expose live session info.
session_mgr: "SessionManager | None" = None
