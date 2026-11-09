"""Throttled progress-bar rendering for a Telegram status message."""
from __future__ import annotations

import time
from dataclasses import dataclass

from pyrogram import Client
from pyrogram.errors import FloodWait, MessageNotModified


def render_bar(done: int, total: int, width: int = 18) -> str:
    """Return a unicode progress bar like ``███████░░░░░░░░░░░ 41%``."""
    if total <= 0:
        return "░" * width + " 0%"
    ratio = max(0.0, min(1.0, done / total))
    filled = int(round(ratio * width))
    bar = "█" * filled + "░" * (width - filled)
    return f"{bar} {int(ratio * 100)}%"


def _fmt_eta(seconds: float) -> str:
    seconds = int(max(0, seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


@dataclass
class ProgressReporter:
    """Edits a single message with a live progress bar, throttled by time.

    Telegram rate-limits message edits, so we only actually push an update
    every ``interval`` seconds (plus a forced update on completion).
    """

    client: Client
    chat_id: int
    message_id: int
    header: str
    interval: float = 2.5

    def __post_init__(self) -> None:
        self._start = time.monotonic()
        self._last_edit = 0.0

    async def update(self, done: int, total: int, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_edit) < self.interval:
            return
        self._last_edit = now

        elapsed = now - self._start
        rate = done / elapsed if elapsed > 0 else 0.0
        remaining = (total - done) / rate if rate > 0 else 0.0

        text = (
            f"{self.header}\n\n"
            f"`{render_bar(done, total)}`\n\n"
            f"✅ Copied: **{done} / {total}**\n"
            f"⚡ Speed: **{rate:.1f} msg/s**\n"
            f"⏳ ETA: **{_fmt_eta(remaining)}**"
        )
        await self._safe_edit(text)

    async def finish(self, done: int, total: int, elapsed: float, *, failed: int = 0) -> None:
        copied = done - failed
        text = (
            "✅ **Copy complete!**\n\n"
            f"`{render_bar(done, total)}`\n\n"
            f"📦 Messages copied: **{copied} / {total}**\n"
            f"⏱ Took: **{_fmt_eta(elapsed)}**"
        )
        if failed:
            text += f"\n⚠️ Skipped due to errors: **{failed}** (see server logs)"
        await self._safe_edit(text)

    async def fail(self, message: str) -> None:
        await self._safe_edit(f"❌ **Copy stopped.**\n\n{message}")

    async def _safe_edit(self, text: str) -> None:
        try:
            await self.client.edit_message_text(self.chat_id, self.message_id, text)
        except MessageNotModified:
            pass
        except FloodWait as exc:
            # Don't block the copy loop for a cosmetic edit; just skip this update.
            self._last_edit = time.monotonic() + float(exc.value)
