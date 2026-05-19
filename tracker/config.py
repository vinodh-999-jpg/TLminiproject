"""
config.py — Loads all settings from .env and provides a global Config object.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Walk up to find .env (supports running from any subdirectory)
_base = Path(__file__).parent.parent
load_dotenv(_base / ".env", override=False)
load_dotenv(_base / ".env.example", override=False)  # fallback defaults


class Config:
    # Clockify
    CLOCKIFY_API_KEY: str = os.getenv("CLOCKIFY_API_KEY", "")
    CLOCKIFY_WORKSPACE_ID: str = os.getenv("CLOCKIFY_WORKSPACE_ID", "")
    CLOCKIFY_DEFAULT_PROJECT_ID: str = os.getenv("CLOCKIFY_DEFAULT_PROJECT_ID", "")
    CLOCKIFY_BASE_URL: str = "https://api.clockify.me/api/v1"

    # Tracker
    IDLE_THRESHOLD_SECONDS: int = int(os.getenv("IDLE_THRESHOLD_MINUTES", 5)) * 60
    MIN_SESSION_SECONDS: int = int(os.getenv("MIN_SESSION_SECONDS", 30))
    MERGE_WINDOW_SECONDS: int = int(os.getenv("MERGE_WINDOW_MINUTES", 2)) * 60
    POLL_INTERVAL_SECONDS: int = int(os.getenv("POLL_INTERVAL_SECONDS", 2))

    # Battery
    BATTERY_LOW_THRESHOLD: int = int(os.getenv("BATTERY_LOW_THRESHOLD", 20))
    POLL_INTERVAL_BATTERY_NORMAL: int = int(os.getenv("POLL_INTERVAL_BATTERY_NORMAL", 5))
    POLL_INTERVAL_BATTERY_LOW: int = int(os.getenv("POLL_INTERVAL_BATTERY_LOW", 15))

    # Backend
    BACKEND_HOST: str = os.getenv("BACKEND_HOST", "127.0.0.1")
    BACKEND_PORT: int = int(os.getenv("BACKEND_PORT", 8000))

    # Database
    DATABASE_PATH: Path = Path(os.getenv("DATABASE_PATH", str(_base / "data" / "tracker.db")))

    @classmethod
    def reload(cls):
        """Re-read .env at runtime (e.g., after settings page update)."""
        load_dotenv(_base / ".env", override=True)
        for key, val in cls.__dict__.items():
            if not key.startswith("_") and isinstance(val, (str, int, Path)):
                env_val = os.getenv(key)
                if env_val is not None:
                    setattr(cls, key, type(val)(env_val))


cfg = Config()
