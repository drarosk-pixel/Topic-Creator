"""Finite-state machine definitions and the per-user session store.

The whole bot is single-owner, but we still key everything by user id so the
state transitions stay explicit and nothing can be skipped or run out of order.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


class State(Enum):
    """Every step the owner can be in. Transitions are strictly linear."""

    IDLE = auto()

    # --- login sub-flow ---
    LOGIN_PHONE = auto()
    LOGIN_CODE = auto()
    LOGIN_PASSWORD = auto()

    # --- copy sub-flow ---
    ASK_SOURCE = auto()
    ASK_DEST = auto()
    CHOOSING_TOPIC = auto()
    ASK_NEW_TOPIC_TITLE = auto()
    CONFIRM = auto()
    COPYING = auto()

    # --- settings sub-flow ---
    SET_WORD_REPLACE = auto()
    SET_LINK_REPLACE = auto()
    SET_CAPTION_TEXT = auto()


@dataclass
class TopicRef:
    id: int
    title: str


@dataclass
class Session:
    """Mutable per-user state. One instance per Telegram user id."""

    user_id: int
    state: State = State.IDLE

    # login
    phone: Optional[str] = None
    phone_code_hash: Optional[str] = None

    # copy flow
    source_id: Optional[int] = None
    source_title: Optional[str] = None
    dest_id: Optional[int] = None
    dest_title: Optional[str] = None
    topics: list[TopicRef] = field(default_factory=list)
    topic_page: int = 0
    chosen_topic: Optional[TopicRef] = None

    # a message id we keep editing (topic list / confirm / progress)
    panel_message_id: Optional[int] = None

    # transient: caption text awaiting a mode choice in the settings flow
    pending_caption: Optional[str] = None

    def reset_flow(self) -> None:
        """Clear the copy flow but keep login state untouched."""
        self.state = State.IDLE
        self.source_id = None
        self.source_title = None
        self.dest_id = None
        self.dest_title = None
        self.topics = []
        self.topic_page = 0
        self.chosen_topic = None
        self.panel_message_id = None
        self.pending_caption = None

    def reset_login(self) -> None:
        self.phone = None
        self.phone_code_hash = None


class SessionStore:
    """In-memory session registry keyed by Telegram user id."""

    def __init__(self) -> None:
        self._sessions: dict[int, Session] = {}

    def get(self, user_id: int) -> Session:
        session = self._sessions.get(user_id)
        if session is None:
            session = Session(user_id=user_id)
            self._sessions[user_id] = session
        return session
