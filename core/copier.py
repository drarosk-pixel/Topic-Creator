"""The copy engine: read a source chat and clone its content into a topic.

Design goals from the spec: highly accurate (nothing skipped, correct order,
albums kept together) and as fast as Telegram allows. We cannot make FloodWait
disappear — Telegram's limits are server-side and undocumented — but an
adaptive throttle keeps the copy running at the fastest rate that avoids them
in practice, and a checkpoint lets an interrupted copy resume without creating
duplicates.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from functools import partial
from pathlib import Path
from typing import Awaitable, Callable, Optional, TypeVar

from pyrogram import Client
from pyrogram.errors import FloodWait

from core.progress import ProgressReporter
from core.states import TopicRef

log = logging.getLogger(__name__)

T = TypeVar("T")


# --------------------------------------------------------------------------- #
# Adaptive throttle
# --------------------------------------------------------------------------- #
class AdaptiveThrottle:
    """Self-tuning delay between copies.

    Starts at ``base_delay``. When Telegram hands out a FloodWait we slow down
    (so the next ones are smaller or gone); after a long clean streak we speed
    back up a little. The result settles near the fastest rate the account
    tolerates without FloodWaits.
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
        """A FloodWait happened — back off hard."""
        self.delay = min(self.max, self.delay * 1.6 + 0.5)
        self._ok_streak = 0

    def reward(self) -> None:
        """A clean copy — after a streak, shave a little off the delay."""
        self._ok_streak += 1
        if self._ok_streak >= 30 and self.delay > self.min:
            self.delay = max(self.min, self.delay - 0.1)
            self._ok_streak = 0


# --------------------------------------------------------------------------- #
# Generic FloodWait retry (for one-off calls like get_chat / topics)
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


async def resolve_chat(client: Client, chat_id):
    return await with_floodwait(lambda: client.get_chat(chat_id))


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
    """Return ordered copy units, oldest first.

    Each unit is ``{"anchor": int, "ids": [int, ...], "is_album": bool}``.
    Album members are grouped so the album is copied once, in order, and the
    whole album is counted when deciding resume points.
    """
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
    floodwait_max_retries: int = 12,
) -> tuple[int, int, float]:
    """Copy all source messages into the destination topic.

    Returns ``(copied_or_skipped_count, total, elapsed_seconds)``.
    """
    start = time.monotonic()
    throttle = AdaptiveThrottle(copy_delay)

    await reporter._safe_edit(f"{reporter.header}\n\n🔎 Reading source channel…")
    units = await _collect_units(client, source_id)
    total = sum(len(u["ids"]) for u in units)

    if total == 0:
        await reporter.fail("The source channel has no copyable messages.")
        return 0, 0, time.monotonic() - start

    # Resume point.
    last_id = 0
    done = 0
    ckpt_file = (
        checkpoint_path(ckpt_dir, source_id, dest_id, topic_id) if ckpt_dir is not None else None
    )
    if resume and ckpt_file is not None:
        saved = load_checkpoint(ckpt_dir, source_id, dest_id, topic_id)
        if saved:
            last_id = int(saved["last_id"])
            done = int(saved["done"])

    failed = 0
    await reporter.update(done, total, force=True)

    for unit in units:
        unit_max = max(unit["ids"])
        size = len(unit["ids"])

        # Already copied in a previous (interrupted) run.
        if unit_max <= last_id:
            continue

        anchor = unit["anchor"]
        if unit["is_album"]:
            action = partial(
                client.copy_media_group,
                chat_id=dest_id,
                from_chat_id=source_id,
                message_id=anchor,
                message_thread_id=topic_id,
            )
        else:
            action = partial(
                client.copy_message,
                chat_id=dest_id,
                from_chat_id=source_id,
                message_id=anchor,
                message_thread_id=topic_id,
            )

        # Copy with adaptive FloodWait handling.
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
                    log.error("Giving up on unit %s after %d FloodWaits", anchor, attempt)
                    break
                wait = float(exc.value) + 1.0
                log.warning("FloodWait %0.fs on unit %s (attempt %d); delay now %.2fs",
                            wait, anchor, attempt, throttle.delay)
                await asyncio.sleep(wait)
            except Exception as exc:  # noqa: BLE001 - one bad unit must not abort the job
                failed += size
                log.warning("Failed to copy unit %s: %s", anchor, exc)
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
