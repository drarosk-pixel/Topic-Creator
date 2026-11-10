"""The copy engine: read a source chat and clone its content into a topic.

Fast path (no transforms): copy by reference, no download.
Transform path (word/link replace, caption, thumbnail): fetch each message,
rewrite text/caption (HTML) and optionally re-upload with a new thumbnail.

Accuracy: nothing skipped, correct order, albums kept together. Speed: an
adaptive throttle keeps the copy near the fastest rate the account tolerates
without FloodWaits, and a checkpoint lets interrupted copies resume without
duplicates.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from functools import partial
from pathlib import Path
from typing import Awaitable, Callable, Optional, TypeVar

from pyrogram import Client, enums
from pyrogram.errors import FloodWait
from pyrogram.types import (
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
)

from core.progress import ProgressReporter
from core.settings import Settings, transform_text
from core.states import TopicRef

log = logging.getLogger(__name__)

T = TypeVar("T")
_HTML = enums.ParseMode.HTML


# --------------------------------------------------------------------------- #
# Adaptive throttle
# --------------------------------------------------------------------------- #
class AdaptiveThrottle:
    """Self-tuning delay between copies.

    Starts at ``base_delay``. A FloodWait makes it back off; a long clean streak
    speeds it back up. It settles near the fastest rate the account tolerates.
    """

    def __init__(self, base_delay: float, *, min_delay: float = 0.3, max_delay: float = 8.0) -> None:
        self.min = max(0.0, min_delay)
        self.max = max_delay
        self.delay = min(self.max, max(self.min, base_delay))
        self._ok_streak = 0

    async def pause(self) -> None:
        if self.delay > 0:
            await asyncio.sleep(self.delay)

    def penalize(self) -> None:
        self.delay = min(self.max, self.delay * 1.6 + 0.5)
        self._ok_streak = 0

    def reward(self) -> None:
        self._ok_streak += 1
        if self._ok_streak >= 30 and self.delay > self.min:
            self.delay = max(self.min, self.delay - 0.1)
            self._ok_streak = 0


# --------------------------------------------------------------------------- #
# Generic FloodWait + transient retry (one-off calls)
# --------------------------------------------------------------------------- #
async def with_floodwait(factory: Callable[[], Awaitable[T]], *, max_retries: int = 8) -> T:
    attempt = 0
    while True:
        try:
            return await factory()
        except FloodWait as exc:
            attempt += 1
            if attempt > max_retries:
                raise
            wait = float(exc.value) + 1.0
            log.warning("FloodWait: sleeping %.0fs (attempt %d)", wait, attempt)
            await asyncio.sleep(wait)


async def resolve_chat(client: Client, chat_id, *, attempts: int = 3):
    """Resolve a chat, retrying transient timeouts (common for cold peers)."""
    last_exc: Optional[Exception] = None
    for i in range(attempts):
        try:
            return await with_floodwait(lambda: client.get_chat(chat_id))
        except (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError) as exc:
            last_exc = exc
            log.warning("get_chat(%s) transient error (try %d/%d): %s", chat_id, i + 1, attempts, exc)
            await asyncio.sleep(2.0 * (i + 1))
    assert last_exc is not None
    raise last_exc


async def fetch_topics(client: Client, dest_id: int) -> list[TopicRef]:
    topics: list[TopicRef] = []
    async for topic in client.get_forum_topics(dest_id):
        title = getattr(topic, "title", None) or f"Topic {topic.id}"
        topics.append(TopicRef(id=topic.id, title=title))
    return topics


async def create_topic(client: Client, dest_id: int, title: str) -> TopicRef:
    topic = await with_floodwait(lambda: client.create_forum_topic(dest_id, title))
    created_title = getattr(topic, "title", None) or title
    return TopicRef(id=topic.id, title=created_title)


# --------------------------------------------------------------------------- #
# Checkpoints (resume / duplicate protection)
# --------------------------------------------------------------------------- #
def checkpoint_path(ckpt_dir: Path, source_id: int, dest_id: int, topic_id: int) -> Path:
    return ckpt_dir / f"ckpt_{source_id}_{dest_id}_{topic_id}.json"


def load_checkpoint(ckpt_dir: Path, source_id: int, dest_id: int, topic_id: int) -> Optional[dict]:
    path = checkpoint_path(ckpt_dir, source_id, dest_id, topic_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if {"last_id", "done", "total"} <= data.keys():
            return data
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return None


def clear_checkpoint(ckpt_dir: Path, source_id: int, dest_id: int, topic_id: int) -> None:
    path = checkpoint_path(ckpt_dir, source_id, dest_id, topic_id)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def _save_checkpoint(path: Path, last_id: int, done: int, total: int) -> None:
    try:
        path.write_text(
            json.dumps({"last_id": last_id, "done": done, "total": total}),
            encoding="utf-8",
        )
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Source scanning -> ordered "units" (single message or whole album)
# --------------------------------------------------------------------------- #
async def _collect_units(client: Client, source_id: int) -> list[dict]:
    raw: list[tuple[int, Optional[str]]] = []
    async for message in client.get_chat_history(source_id):
        if getattr(message, "service", None) or getattr(message, "empty", False):
            continue
        raw.append((message.id, message.media_group_id))
    raw.sort(key=lambda t: t[0])

    units: list[dict] = []
    by_group: dict[str, dict] = {}
    for mid, mgid in raw:
        if mgid is None:
            units.append({"anchor": mid, "ids": [mid], "is_album": False})
        elif mgid in by_group:
            by_group[mgid]["ids"].append(mid)
        else:
            unit = {"anchor": mid, "ids": [mid], "is_album": True}
            by_group[mgid] = unit
            units.append(unit)
    return units


# --------------------------------------------------------------------------- #
# Sending (fast path vs transform path)
# --------------------------------------------------------------------------- #
def _media_type(msg) -> Optional[str]:
    for name in ("video", "document", "animation", "photo", "audio", "voice", "sticker", "video_note"):
        if getattr(msg, name, None):
            return name
    return None


def _build_input_media(m, caption: str):
    if m.photo:
        return InputMediaPhoto(m.photo.file_id, caption=caption, parse_mode=_HTML)
    if m.video:
        return InputMediaVideo(m.video.file_id, caption=caption, parse_mode=_HTML)
    if m.document:
        return InputMediaDocument(m.document.file_id, caption=caption, parse_mode=_HTML)
    if m.audio:
        return InputMediaAudio(m.audio.file_id, caption=caption, parse_mode=_HTML)
    return None


async def _resend_with_thumbnail(
    client: Client, msg, *, dest_id: int, topic_id: int, caption: str, thumb: str, media_type: str
) -> None:
    tmp = str(Path(thumb).parent / f"dl_{uuid.uuid4().hex}")
    path = await client.download_media(msg, file_name=tmp)
    try:
        if media_type == "video":
            v = msg.video
            await client.send_video(
                dest_id, path, thumb=thumb, caption=caption, parse_mode=_HTML,
                duration=getattr(v, "duration", 0) or 0,
                width=getattr(v, "width", 0) or 0,
                height=getattr(v, "height", 0) or 0,
                supports_streaming=True, message_thread_id=topic_id,
            )
        elif media_type == "animation":
            a = msg.animation
            await client.send_animation(
                dest_id, path, thumb=thumb, caption=caption, parse_mode=_HTML,
                duration=getattr(a, "duration", 0) or 0,
                width=getattr(a, "width", 0) or 0,
                height=getattr(a, "height", 0) or 0,
                message_thread_id=topic_id,
            )
        else:  # document
            d = msg.document
            await client.send_document(
                dest_id, path, thumb=thumb, caption=caption, parse_mode=_HTML,
                file_name=getattr(d, "file_name", None), message_thread_id=topic_id,
            )
    finally:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


async def _send_single_transformed(
    client: Client, anchor: int, *, source_id: int, dest_id: int, topic_id: int, settings: Settings
) -> None:
    msg = await client.get_messages(source_id, anchor)

    if not msg.media:  # text message
        new_text = transform_text(msg.text.html if msg.text else "", settings)
        await client.send_message(dest_id, new_text, parse_mode=_HTML, message_thread_id=topic_id)
        return

    original_caption = msg.caption.html if msg.caption else ""
    new_caption = transform_text(original_caption, settings)
    media_type = _media_type(msg)

    if settings.thumbnail_path and media_type in ("video", "document", "animation"):
        await _resend_with_thumbnail(
            client, msg, dest_id=dest_id, topic_id=topic_id,
            caption=new_caption, thumb=settings.thumbnail_path, media_type=media_type,
        )
    else:
        await client.copy_message(
            chat_id=dest_id, from_chat_id=source_id, message_id=anchor,
            caption=new_caption, parse_mode=_HTML, message_thread_id=topic_id,
        )


async def _send_album_transformed(
    client: Client, anchor: int, *, source_id: int, dest_id: int, topic_id: int, settings: Settings
) -> None:
    msgs = await client.get_media_group(source_id, anchor)
    original_caption = ""
    for m in msgs:
        if m.caption:
            original_caption = m.caption.html
            break
    new_caption = transform_text(original_caption, settings)

    items = []
    for i, m in enumerate(msgs):
        media = _build_input_media(m, new_caption if i == 0 else "")
        if media is None:
            # Unsupported media in album: fall back to a plain reference copy.
            await client.copy_media_group(
                chat_id=dest_id, from_chat_id=source_id, message_id=anchor,
                captions=new_caption, message_thread_id=topic_id,
            )
            return
        items.append(media)
    await client.send_media_group(dest_id, items, message_thread_id=topic_id)


async def _send_unit(
    client: Client, unit: dict, *, source_id: int, dest_id: int, topic_id: int,
    settings: Optional[Settings],
) -> None:
    anchor = unit["anchor"]
    if settings is None or not settings.has_transforms():
        if unit["is_album"]:
            await client.copy_media_group(
                chat_id=dest_id, from_chat_id=source_id, message_id=anchor,
                message_thread_id=topic_id,
            )
        else:
            await client.copy_message(
                chat_id=dest_id, from_chat_id=source_id, message_id=anchor,
                message_thread_id=topic_id,
            )
        return

    if unit["is_album"]:
        await _send_album_transformed(
            client, anchor, source_id=source_id, dest_id=dest_id, topic_id=topic_id, settings=settings
        )
    else:
        await _send_single_transformed(
            client, anchor, source_id=source_id, dest_id=dest_id, topic_id=topic_id, settings=settings
        )


# --------------------------------------------------------------------------- #
# Main copy routine
# --------------------------------------------------------------------------- #
async def run_copy(
    client: Client,
    *,
    source_id: int,
    dest_id: int,
    topic_id: int,
    reporter: ProgressReporter,
    copy_delay: float = 1.0,
    ckpt_dir: Optional[Path] = None,
    resume: bool = False,
    settings: Optional[Settings] = None,
    floodwait_max_retries: int = 12,
) -> tuple[int, int, float]:
    """Copy all source messages into the destination topic.

    Returns ``(done, total, elapsed_seconds)``.
    """
    start = time.monotonic()
    throttle = AdaptiveThrottle(copy_delay)

    await reporter._safe_edit(f"{reporter.header}\n\n🔎 Reading source channel…")
    units = await _collect_units(client, source_id)
    total = sum(len(u["ids"]) for u in units)

    if total == 0:
        await reporter.fail("The source channel has no copyable messages.")
        return 0, 0, time.monotonic() - start

    last_id = 0
    done = 0
    ckpt_file = (
        checkpoint_path(ckpt_dir, source_id, dest_id, topic_id) if ckpt_dir is not None else None
    )
    if resume and ckpt_dir is not None:
        saved = load_checkpoint(ckpt_dir, source_id, dest_id, topic_id)
        if saved:
            last_id = int(saved["last_id"])
            done = int(saved["done"])

    failed = 0
    await reporter.update(done, total, force=True)

    for unit in units:
        unit_max = max(unit["ids"])
        size = len(unit["ids"])

        if unit_max <= last_id:  # already copied in a previous run
            continue

        action = partial(
            _send_unit, client, unit,
            source_id=source_id, dest_id=dest_id, topic_id=topic_id, settings=settings,
        )

        attempt = 0
        while True:
            try:
                await action()
                throttle.reward()
                break
            except FloodWait as exc:
                attempt += 1
                throttle.penalize()
                if attempt > floodwait_max_retries:
                    failed += size
                    log.error("Giving up on unit %s after %d FloodWaits", unit["anchor"], attempt)
                    break
                wait = float(exc.value) + 1.0
                log.warning("FloodWait %.0fs on unit %s (attempt %d); delay now %.2fs",
                            wait, unit["anchor"], attempt, throttle.delay)
                await asyncio.sleep(wait)
            except Exception as exc:  # noqa: BLE001 - one bad unit must not abort the job
                failed += size
                log.warning("Failed to copy unit %s: %s", unit["anchor"], exc)
                break

        done += size
        last_id = unit_max
        if ckpt_file is not None:
            _save_checkpoint(ckpt_file, last_id, done, total)
        await reporter.update(done, total)
        await throttle.pause()

    elapsed = time.monotonic() - start
    if ckpt_dir is not None:
        clear_checkpoint(ckpt_dir, source_id, dest_id, topic_id)
    await reporter.finish(done, total, elapsed, failed=failed)
    return done, total, elapsed
