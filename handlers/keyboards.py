"""Inline keyboard builders for the copy flow."""
from __future__ import annotations

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.states import TopicRef

TOPICS_PER_PAGE = 6


def topics_keyboard(topics: list[TopicRef], page: int) -> InlineKeyboardMarkup:
    total = len(topics)
    pages = max(1, (total + TOPICS_PER_PAGE - 1) // TOPICS_PER_PAGE)
    page = max(0, min(page, pages - 1))
    start = page * TOPICS_PER_PAGE
    chunk = topics[start : start + TOPICS_PER_PAGE]

    rows: list[list[InlineKeyboardButton]] = []
    for topic in chunk:
        label = topic.title if len(topic.title) <= 40 else topic.title[:39] + "…"
        rows.append([InlineKeyboardButton(f"📌 {label}", callback_data=f"topic:{topic.id}")])

    if pages > 1:
        nav: list[InlineKeyboardButton] = []
        nav.append(
            InlineKeyboardButton("◀️", callback_data=f"pg:{page - 1}")
            if page > 0
            else InlineKeyboardButton(" ", callback_data="noop")
        )
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"))
        nav.append(
            InlineKeyboardButton("▶️", callback_data=f"pg:{page + 1}")
            if page < pages - 1
            else InlineKeyboardButton(" ", callback_data="noop")
        )
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton("➕ New topic", callback_data="newtopic"),
            InlineKeyboardButton("🔄 Refresh", callback_data="refresh"),
        ]
    )
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


def confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Copy", callback_data="confirm:copy")],
            [
                InlineKeyboardButton("🔙 Back", callback_data="confirm:back"),
                InlineKeyboardButton("❌ Cancel", callback_data="confirm:cancel"),
            ],
        ]
    )
