"""Application settings, read from the environment (and .env) at startup."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _num(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


PLACEHOLDER_KEYS = {"", "your_key_here", "your-key-here", "changeme"}


def _key(name: str) -> str:
    v = (os.getenv(name) or "").strip().strip("\"'")
    return "" if v.lower() in PLACEHOLDER_KEYS else v


class Settings:
    def __init__(self) -> None:
        # GOOGLE_API_KEY first, then GEMINI_API_KEY. Never expose or log this value.
        # The template placeholder from .env.example does not count as a configured key.
        self.api_key = _key("GOOGLE_API_KEY") or _key("GEMINI_API_KEY")
        self.live_model = os.getenv("LIVE_TRANSCRIPTION_MODEL", "gemini-3.5-transcribe-live")
        self.upload_model = os.getenv("UPLOAD_TRANSCRIPTION_MODEL", "gemini-2.5-flash")
        self.analysis_model = os.getenv("ANALYSIS_MODEL", "gemini-3.1-flash-lite")
        self.timezone = os.getenv("CALL_TIMEZONE", "Asia/Kolkata")  # used to resolve "today/tomorrow" in conversations
        self.host = os.getenv("HOST", "127.0.0.1")
        self.port = int(_num("PORT", 8000))
        self.data_dir = Path(os.getenv("REPASSIST_DATA_DIR") or (BASE_DIR / "data"))
        self.templates_dir = BASE_DIR / "configuration_templates"
        self.save_sessions = _bool("SAVE_SESSIONS", True)
        self.save_live_audio = _bool("SAVE_LIVE_AUDIO", False)
        self.retention_days = _num("RETENTION_DAYS", 0)
        self.max_upload_mb = _num("MAX_UPLOAD_MB", 100)
        self.embed_audio_max_mb = _num("EMBED_AUDIO_MAX_MB", 20)
        # Analysis scheduler tuning (seconds)
        self.debounce_s = _num("ANALYSIS_DEBOUNCE_S", 1.0)
        self.max_wait_s = _num("ANALYSIS_MAX_WAIT_S", 4.0)

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def configs_dir(self) -> Path:
        return self.data_dir / "configurations"
