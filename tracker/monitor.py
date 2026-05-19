"""
monitor.py — Active window + browser URL detection for Windows.

Supported browsers for URL extraction:
  Chrome, Firefox, Edge (Chromium), Brave
"""
from __future__ import annotations

import re
import time
import logging
from dataclasses import dataclass
from typing import Optional

import psutil
import win32gui
import win32process

log = logging.getLogger(__name__)

# Browser process names → address bar UI element title (for pywinauto)
BROWSER_PROFILES: dict[str, dict] = {
    "chrome.exe":    {"name": "Address and search bar"},
    "msedge.exe":    {"name": "Address and search bar"},
    "brave.exe":     {"name": "Address and search bar"},
    "firefox.exe":   {"name": "Search with Google or enter address"},
    "opera.exe":     {"name": "Address and search bar"},
    "vivaldi.exe":   {"name": "Address and search bar"},
}

BROWSER_NAMES = set(BROWSER_PROFILES.keys())


@dataclass
class WindowInfo:
    app_name: str
    process: str
    window_title: str
    url: Optional[str]
    domain: Optional[str]
    pid: int


def _get_pid_for_hwnd(hwnd: int) -> int:
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid
    except Exception:
        return 0


def _get_process_name(pid: int) -> str:
    try:
        proc = psutil.Process(pid)
        return proc.name()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return ""


def _get_app_display_name(process: str) -> str:
    """Map process name to a human-friendly app name."""
    mapping = {
        "code.exe": "VS Code",
        "code": "VS Code",
        "pycharm64.exe": "PyCharm",
        "idea64.exe": "IntelliJ IDEA",
        "webstorm64.exe": "WebStorm",
        "devenv.exe": "Visual Studio",
        "chrome.exe": "Google Chrome",
        "msedge.exe": "Microsoft Edge",
        "firefox.exe": "Firefox",
        "brave.exe": "Brave",
        "zoom.exe": "Zoom",
        "Teams.exe": "Microsoft Teams",
        "slack.exe": "Slack",
        "Discord.exe": "Discord",
        "OUTLOOK.EXE": "Outlook",
        "WindowsTerminal.exe": "Windows Terminal",
        "powershell.exe": "PowerShell",
        "cmd.exe": "Command Prompt",
        "pwsh.exe": "PowerShell 7",
        "WINWORD.EXE": "Microsoft Word",
        "EXCEL.EXE": "Microsoft Excel",
        "POWERPNT.EXE": "PowerPoint",
        "obsidian.exe": "Obsidian",
        "notion.exe": "Notion",
        "Spotify.exe": "Spotify",
        "explorer.exe": "File Explorer",
    }
    return mapping.get(process, process.replace(".exe", "").title())


def _extract_domain(url: str) -> Optional[str]:
    if not url:
        return None
    match = re.search(r"(?:https?://)?(?:www\.)?([^/\s?#]+)", url)
    return match.group(1).lower() if match else None


def _get_browser_url(process: str, hwnd: int) -> Optional[str]:
    """
    Try to extract the active tab URL from a browser window via
    Windows UI Automation (pywinauto). Returns None on any failure.
    """
    try:
        from pywinauto import Application, findwindows  # noqa: import inside function (lazy)
        profile = BROWSER_PROFILES.get(process.lower())
        if not profile:
            return None

        app = Application(backend="uia").connect(handle=hwnd)
        window = app.top_window()
        addr_bar = window.child_window(
            title=profile["name"],
            control_type="Edit",
            found_index=0,
        )
        url = addr_bar.get_value()
        # If it's a search query (no dot), don't treat it as a URL
        if url and "." in url and not url.startswith("about:"):
            if not url.startswith("http"):
                url = "https://" + url
            return url
    except Exception:
        pass
    return None


def _parse_url_from_title(title: str, process: str) -> Optional[str]:
    """
    Fallback: parse domain from window title for browsers.
    e.g. 'GitHub - Microsoft Edge' → 'github.com'
    """
    if process.lower() not in BROWSER_NAMES:
        return None
    # Remove browser suffix
    title = re.sub(r"\s*[-–|]\s*(Google Chrome|Microsoft Edge|Firefox|Brave|Opera).*$", "", title, flags=re.IGNORECASE)
    # Look for domain-like patterns in title
    domain_match = re.search(r"([a-zA-Z0-9-]+\.[a-zA-Z]{2,})", title)
    if domain_match:
        return "https://" + domain_match.group(1)
    return None


def get_active_window() -> Optional[WindowInfo]:
    """
    Returns info about the currently focused window.
    Returns None if window cannot be determined.
    """
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None

        title = win32gui.GetWindowText(hwnd)
        if not title:
            return None

        pid = _get_pid_for_hwnd(hwnd)
        process = _get_process_name(pid)
        app_name = _get_app_display_name(process)
        proc_lower = process.lower()

        url: Optional[str] = None
        domain: Optional[str] = None

        if proc_lower in BROWSER_NAMES:
            url = _get_browser_url(proc_lower, hwnd)
            if not url:
                url = _parse_url_from_title(title, proc_lower)
            domain = _extract_domain(url) if url else None

        return WindowInfo(
            app_name=app_name,
            process=process,
            window_title=title,
            url=url,
            domain=domain,
            pid=pid,
        )
    except Exception as e:
        log.debug(f"get_active_window error: {e}")
        return None
