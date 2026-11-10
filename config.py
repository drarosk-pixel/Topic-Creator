"""Configuration loaded from environment / .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def _require_int(name: str) -> int:
    raw = _require(name)
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    bot_token: str
    owner_id: int
    sessions_dir: Path
    progress_edit_interval: float
    copy_delay: float

    @classmethod
    def load(cls) -> "Config":
        sessions_dir = Path(os.getenv("SESSIONS_DIR", "sessions").strip() or "sessions")
        sessions_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            api_id=_require_int("API_ID"),
            api_hash=_require("API_HASH"),
            bot_token=_require("BOT_TOKEN"),
            owner_id=_require_int("OWNER_ID"),
            sessions_dir=sessions_dir,
            progress_edit_interval=float(os.getenv("PROGRESS_EDIT_INTERVAL", "2.5") or 2.5),
            copy_delay=float(os.getenv("COPY_DELAY", "1.0") or 1.0),
        )

    @property
    def session_string_file(self) -> Path:
        return self.sessions_dir / "userbot.session_string"

    @property
    def settings_file(self) -> Path:
        return self.sessions_dir / "settings.json"

    @property
    def thumbnail_file(self) -> Path:
        return self.sessions_dir / "thumb.jpg"
