"""Shared application context passed to every handler module."""
from __future__ import annotations

from dataclasses import dataclass

from config import Config
from core.states import SessionStore
from core.userbot import UserBot


@dataclass
class AppContext:
    cfg: Config
    store: SessionStore
    userbot: UserBot
