"""The copy engine: read a source chat and clone its content into a topic.

Design goals from the spec: highly accurate (nothing skipped, correct order,
albums kept together) and fast (sequential with FloodWait-aware retries and a
small configurable delay).
"""
from __future__ import annotations

import asyncio
import logging
import time
from functools import partial
from typing import Awaitable, Callable, Optional, TypeVar

from pyrogram import Client
from pyrogram.errors import FloodWait

from core.progress import ProgressReporter
from core.states import TopicRef

log = logging.getLogger(__name__)

T = TypeVar("T")


async def with_floodwait(factory: Callable[[], Awaitable[T]], *, max_retries: int = 5) -> T:
    """Run an awaitable, transparently waiting out FloodWait errors."""
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


async def resolve_chat(client: Client, chat_id: int):
    """Return the chat object, raising a clear error if it can't be reached."""
    return await with_floodwait(lambda: client.get_chat(chat_id))


async def fetch_topics(client: Client, dest_id: int) -> list[TopicRef]:
    """Return every forum topic of the destination, newest activity first."""
    topics: list[TopicRef] = []
    async for topic in client.get_forum_topics(dest_id):
        title = getattr(topic, "title", None) or f"Topic {topic.id}"
        topics.append(TopicRef(id=topic.id, title=title))
    return topics


async def create_topic(client: Client, dest_id: int, title: str) -> TopicRef:
    topic = await with_floodwait(lambda: client.create_forum_topic(dest_id, title))
    created_title = getattr(topic, "title", None) or title
    return TopicRef(id=topic.id, title=created_title)


async def _collect_source(client: Client, source_id: int) -> list[tuple[int, Optional[str]]]:
    """Collect (message_id, media_group_id) for every real message, oldest first.

    Service messages are skipped so they neither get copied nor distort the
    progress total.
    """
    items: list[tuple[int, Optional[str]]] = []
    async for message in client.get_chat_history(source_id):
        if getattr(message, "service", None):
            continue
        if getattr(message, "empty", False):
            continue
        items.append((message.id, message.media_group_id))
    items.sort(key=lambda t: t[0])  # chronological order (oldest -> newest)
    return items


async def run_copy(
    client: Client,
    *,
    source_id: int,
    dest_id: int,
    topic_id: int,
    reporter: ProgressReporter,
    copy_delay: float = 0.4,
) -> tuple[int, int, float]:
    """Copy all source messages into the destination topic.

    Returns ``(done, total, elapsed_seconds)``.
    """
    start = time.monotonic()

    await reporter._safe_edit(f"{reporter.header}\n\n🔎 Reading source channel…")
    items = await _collect_source(client, source_id)
    total = len(items)

    if total == 0:
        await reporter.fail("The source channel has no copyable messages.")
        return 0, 0, time.monotonic() - start

    done = 0
    failed = 0
    processed_groups: set[str] = set()

    await reporter.update(done, total, force=True)

    for message_id, media_group_id in items:
        if media_group_id is not None:
            if media_group_id in processed_groups:
                # Already copied as part of its album; just advance the counter.
                done += 1
                await reporter.update(done, total)
                continue
            processed_groups.add(media_group_id)
            action = partial(
                client.copy_media_group,
                chat_id=dest_id,
                from_chat_id=source_id,
                message_id=message_id,
                message_thread_id=topic_id,
            )
        else:
            action = partial(
                client.copy_message,
                chat_id=dest_id,
                from_chat_id=source_id,
                message_id=message_id,
                message_thread_id=topic_id,
            )

        try:
            await with_floodwait(action)
        except Exception as exc:  # noqa: BLE001 - one bad message must not abort the whole job
            failed += 1
            log.warning("Failed to copy message %s: %s", message_id, exc)

        done += 1
        await reporter.update(done, total)
        if copy_delay > 0:
            await asyncio.sleep(copy_delay)

    elapsed = time.monotonic() - start
    await reporter.finish(done, total, elapsed, failed=failed)
    return done, total, elapsed
