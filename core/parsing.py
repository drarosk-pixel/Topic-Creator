"""Helpers for parsing user-supplied chat identifiers."""
from __future__ import annotations

import re
from typing import Union

ChatId = Union[int, str]


def parse_chat_id(raw: str) -> ChatId:
    """Turn user input into something Pyrogram's ``get_chat`` accepts.

    Accepts:
      * numeric ids such as ``-1001234567890`` or ``1234567890``
      * ``@username``
      * ``t.me/username`` / ``https://t.me/username`` links
    Raises ``ValueError`` for anything else.
    """
    text = raw.strip()
    if not text:
        raise ValueError("empty input")

    # t.me link -> username
    link = re.match(r"^(?:https?://)?t\.me/(?:s/)?([A-Za-z0-9_]{4,})/?$", text)
    if link:
        return "@" + link.group(1)

    if text.startswith("@"):
        return text

    # plain integer id (with optional leading minus)
    if re.fullmatch(r"-?\d+", text):
        return int(text)

    # bare username without @
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,}", text):
        return "@" + text

    raise ValueError(f"Could not understand chat identifier: {raw!r}")


def strip_code(raw: str) -> str:
    """Extract just the digits of an OTP, tolerating spaces/dashes.

    Telegram invalidates a login code if the exact code text appears in a
    Telegram message, so owners are told to send it spaced out (e.g. ``1 2 3``).
    """
    return re.sub(r"\D", "", raw)
