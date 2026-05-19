"""
battery_guard.py — Adaptive polling interval based on power/battery state.

On AC:           2s  (full accuracy)
On battery >20%: 5s  (balanced)
On battery <20%: 15s (aggressive saving, disables URL scraping)
"""
from __future__ import annotations

import logging
import time
from typing import Tuple

import psutil

log = logging.getLogger(__name__)

# (poll_interval_seconds, url_scraping_enabled)
_AC         = (2,  True)
_BAT_NORMAL = (5,  True)
_BAT_LOW    = (15, False)


class BatteryGuard:
    def __init__(self, low_threshold: int = 20):
        self.low_threshold = low_threshold
        self._last_check: float = 0.0
        self._check_interval: float = 30.0  # re-check battery every 30s
        self._cached: Tuple[int, bool] = _AC

    def get_settings(self) -> Tuple[int, bool]:
        """
        Returns (poll_interval_seconds, url_scraping_enabled).
        Result is cached for 30 seconds to avoid constant battery reads.
        """
        now = time.monotonic()
        if now - self._last_check < self._check_interval:
            return self._cached

        self._last_check = now
        try:
            bat = psutil.sensors_battery()
            if bat is None or bat.power_plugged:
                result = _AC
            elif bat.percent <= self.low_threshold:
                result = _BAT_LOW
                log.info(f"Battery low ({bat.percent:.0f}%) — aggressive power saving active")
            else:
                result = _BAT_NORMAL
        except Exception:
            result = _AC  # Default to AC if can't detect

        self._cached = result
        return result

    @property
    def poll_interval(self) -> int:
        return self.get_settings()[0]

    @property
    def url_scraping_enabled(self) -> bool:
        return self.get_settings()[1]

    @property
    def battery_info(self) -> dict:
        try:
            bat = psutil.sensors_battery()
            if bat is None:
                return {"plugged": True, "percent": 100, "charging": True}
            return {
                "plugged": bat.power_plugged,
                "percent": round(bat.percent, 1),
                "charging": bat.power_plugged,
                "secs_left": bat.secsleft if bat.secsleft != psutil.POWER_TIME_UNLIMITED else None,
            }
        except Exception:
            return {"plugged": True, "percent": 100, "charging": True}
