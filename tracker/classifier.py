"""
classifier.py — Rule-based activity classifier.

Priority:  domain rules > app/process rules > title keyword rules > "Browsing" fallback
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_rules_dir = Path(__file__).parent / "rules"


def _load_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"Could not load rules from {path}: {e}")
        return {}


# ── Load rules at import time (hot-reloadable via reload_rules()) ─────────────
_app_rules: dict[str, str] = {}
_domain_rules: dict[str, str] = {}
_keyword_rules: dict[str, list[str]] = {}


def reload_rules():
    global _app_rules, _domain_rules, _keyword_rules
    app_data = _load_json(_rules_dir / "app_rules.json")
    domain_data = _load_json(_rules_dir / "domain_rules.json")
    _app_rules = {k.lower(): v for k, v in app_data.items()}
    _domain_rules = {k.lower(): v for k, v in domain_data.get("domains", {}).items()}
    _keyword_rules = {
        cat: [kw.lower() for kw in kws]
        for cat, kws in domain_data.get("title_keywords", {}).items()
    }


reload_rules()

# ── Category metadata ─────────────────────────────────────────────────────────
CATEGORY_COLORS = {
    "Coding":         "#6C63FF",
    "DSA Practice":   "#00BFA5",
    "Learning":       "#FF9800",
    "Meetings":       "#2196F3",
    "Communication":  "#9C27B0",
    "Documentation":  "#607D8B",
    "Research":       "#00ACC1",
    "Design":         "#E91E63",
    "Entertainment":  "#F44336",
    "Browsing":       "#78909C",
    "System":         "#90A4AE",
    "Unknown":        "#BDBDBD",
}

PRODUCTIVE_CATEGORIES = {"Coding", "DSA Practice", "Learning", "Research", "Documentation", "Design", "Meetings"}
DISTRACTION_CATEGORIES = {"Entertainment", "Browsing"}


def classify(
    process: str,
    window_title: str,
    domain: Optional[str] = None,
    url: Optional[str] = None,
) -> str:
    """
    Returns the category string for the given activity context.
    """
    proc = process.lower().strip()
    title = window_title.lower().strip()
    dom = (domain or "").lower().strip()

    # 1️⃣  Domain-level match (highest precision)
    if dom:
        # Exact domain
        if dom in _domain_rules:
            return _domain_rules[dom]
        # Subdomain match  e.g. docs.python.org → python.org
        parts = dom.split(".")
        for i in range(len(parts) - 1):
            parent = ".".join(parts[i:])
            if parent in _domain_rules:
                return _domain_rules[parent]

    # 2️⃣  Process / executable name
    if proc in _app_rules:
        return _app_rules[proc]

    # 3️⃣  Title keyword scan
    for category, keywords in _keyword_rules.items():
        for kw in keywords:
            if kw in title:
                return category

    # 4️⃣  Fallback
    return "Browsing" if proc.endswith(".exe") else "Unknown"


def is_productive(category: str) -> bool:
    return category in PRODUCTIVE_CATEGORIES


def is_distraction(category: str) -> bool:
    return category in DISTRACTION_CATEGORIES


def get_color(category: str) -> str:
    return CATEGORY_COLORS.get(category, CATEGORY_COLORS["Unknown"])


def compute_focus_score(sessions: list[dict]) -> float:
    """
    0–100 score:  (productive_sec / total_sec) * 100
    Meetings count at 50% productivity weight.
    """
    total = sum(s.get("duration_sec", 0) for s in sessions)
    if total == 0:
        return 0.0
    productive = sum(
        s["duration_sec"] for s in sessions
        if s.get("category") in PRODUCTIVE_CATEGORIES - {"Meetings"}
    )
    meetings_half = sum(
        s["duration_sec"] * 0.5 for s in sessions
        if s.get("category") == "Meetings"
    )
    return round(min((productive + meetings_half) / total * 100, 100), 1)
