"""The copy flow: pick source -> pick destination -> choose topic -> confirm -> copy.

Plain (undecorated) functions dispatched by the router according to FSM state.
"""
from __future__ import annotations

import logging

from pyrogram import Client
from pyrogram.types import CallbackQuery, Message

from core.app_context import AppContext
from core.copier import (
    clear_checkpoint,
    create_topic,
    fetch_topics,
    load_checkpoint,
    resolve_chat,
    run_copy,
)
from core.parsing import parse_chat_id
from core.progress import ProgressReporter
from core.states import Session, State
from handlers.keyboards import confirm_keyboard, topics_keyboard

log = logging.getLogger(__name__)


def _chat_title(chat) -> str:
    return getattr(chat, "title", None) or getattr(chat, "first_name", None) or str(chat.id)


# ------------------------------------------------------------------ #
# Entry command
# ------------------------------------------------------------------ #
async def cmd_copy(ctx: AppContext, bot: Client, message: Message, session: Session) -> None:
    if not ctx.userbot.is_logged_in:
        await message.reply("🔒 You must /login the userbot first.")
        return
    if session.state is State.COPYING:
        await message.reply("⏳ A copy is already running. Please wait for it to finish.")
        return
    session.reset_flow()
    session.state = State.ASK_SOURCE
    await message.reply(
        "1️⃣ **Source channel**\n\n"
        "Send the channel you want to copy content **from**.\n"
        "Send its ID (e.g. `-1001234567890`), @username, or t.me link.\n\n"
        "Send /cancel to abort."
    )


# ------------------------------------------------------------------ #
# Text steps
# ------------------------------------------------------------------ #
async def on_text(ctx: AppContext, bot: Client, message: Message, session: Session) -> None:
    if session.state is State.ASK_SOURCE:
        await _handle_source(ctx, bot, message, session)
    elif session.state is State.ASK_DEST:
        await _handle_dest(ctx, bot, message, session)
    elif session.state is State.ASK_NEW_TOPIC_TITLE:
        await _handle_new_topic_title(ctx, bot, message, session)


async def _handle_source(ctx: AppContext, bot: Client, message: Message, session: Session) -> None:
    try:
        chat_id = parse_chat_id(message.text or "")
    except ValueError:
        await message.reply("❌ I couldn't read that. Send a channel ID, @username, or t.me link.")
        return
    try:
        chat = await resolve_chat(ctx.userbot.client, chat_id)
    except Exception as exc:  # noqa: BLE001
        await message.reply(
            f"❌ Can't access that chat: `{exc}`\n\n"
            "Make sure your userbot account is a member of it, then try again."
        )
        return
    session.source_id = chat.id
    session.source_title = _chat_title(chat)
    session.state = State.ASK_DEST
    await message.reply(
        f"✅ Source: **{session.source_title}**\n\n"
        "2️⃣ **Destination topic group**\n\n"
        "Send the topic group (forum) you want to copy **into**.\n"
        "Send its ID (e.g. `-1001234567890`), @username, or t.me link.\n\n"
        "Send /cancel to abort."
    )


async def _handle_dest(ctx: AppContext, bot: Client, message: Message, session: Session) -> None:
    try:
        chat_id = parse_chat_id(message.text or "")
    except ValueError:
        await message.reply("❌ I couldn't read that. Send a channel ID, @username, or t.me link.")
        return
    try:
        chat = await resolve_chat(ctx.userbot.client, chat_id)
    except Exception as exc:  # noqa: BLE001
        await message.reply(
            f"❌ Can't access that chat: `{exc}`\n\n"
            "Make sure your userbot account is a member/admin of it, then try again."
        )
        return

    if not getattr(chat, "is_forum", False):
        await message.reply(
            "❌ That group doesn't have **Topics** enabled.\n\n"
            "Open the group settings and turn on *Topics*, create your topics, "
            "then send the group again."
        )
        return

    session.dest_id = chat.id
    session.dest_title = _chat_title(chat)
    await _load_and_show_topics(ctx, bot, message, session)


async def _load_and_show_topics(
    ctx: AppContext, bot: Client, message: Message, session: Session
) -> None:
    notice = await message.reply("🔎 Reading destination and loading its topics…")
    try:
        topics = await fetch_topics(ctx.userbot.client, session.dest_id)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        await notice.edit_text(f"❌ Could not read topics: `{exc}`")
        session.state = State.ASK_DEST
        return

    session.topics = topics
    session.topic_page = 0
    session.state = State.CHOOSING_TOPIC
    session.panel_message_id = notice.id
    await notice.edit_text(_topics_header(session), reply_markup=topics_keyboard(topics, 0))


def _topics_header(session: Session) -> str:
    count = len(session.topics)
    base = (
        f"✅ Source: **{session.source_title}**\n"
        f"✅ Destination: **{session.dest_title}**\n\n"
    )
    if count == 0:
        return base + (
            "3️⃣ **Choose a topic**\n\n"
            "This group has no topics yet. Tap **➕ New topic** to create one."
        )
    return base + f"3️⃣ **Choose a topic** ({count} found):"


async def _handle_new_topic_title(
    ctx: AppContext, bot: Client, message: Message, session: Session
) -> None:
    title = (message.text or "").strip()
    if not title:
        await message.reply("❌ Topic title can't be empty. Send a title, or /cancel.")
        return
    try:
        topic = await create_topic(ctx.userbot.client, session.dest_id, title)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        await message.reply(
            f"❌ Could not create the topic: `{exc}`\n\n"
            "Your userbot account needs *Manage Topics* permission. Send /copy to retry."
        )
        session.reset_flow()
        return
    session.topics.insert(0, topic)
    session.chosen_topic = topic
    session.state = State.CONFIRM
    ckpt = _checkpoint(ctx, session)
    panel = await message.reply(
        _confirm_text(session, ckpt), reply_markup=confirm_keyboard(bool(ckpt))
    )
    session.panel_message_id = panel.id


# ------------------------------------------------------------------ #
# Callback steps
# ------------------------------------------------------------------ #
async def on_callback(ctx: AppContext, bot: Client, cq: CallbackQuery, session: Session) -> None:
    data = cq.data or ""

    if data == "noop":
        await cq.answer()
        return

    if data == "cancel":
        session.reset_flow()
        await cq.answer("Cancelled")
        await cq.message.edit_text("❌ Cancelled. Send /copy to start again.")
        return

    # --- topic-list screen ---
    if session.state is State.CHOOSING_TOPIC:
        await _callback_choosing(ctx, bot, cq, session)
        return

    # --- confirm screen ---
    if session.state is State.CONFIRM:
        await _callback_confirm(ctx, bot, cq, session)
        return

    await cq.answer("This button is no longer active.", show_alert=False)


async def _callback_choosing(
    ctx: AppContext, bot: Client, cq: CallbackQuery, session: Session
) -> None:
    data = cq.data or ""

    if data.startswith("pg:"):
        session.topic_page = int(data.split(":", 1)[1])
        await cq.answer()
        await cq.message.edit_text(
            _topics_header(session),
            reply_markup=topics_keyboard(session.topics, session.topic_page),
        )
        return

    if data == "refresh":
        await cq.answer("Refreshing…")
        try:
            session.topics = await fetch_topics(ctx.userbot.client, session.dest_id)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            await cq.message.edit_text(f"❌ Could not read topics: `{exc}`")
            return
        session.topic_page = 0
        await cq.message.edit_text(
            _topics_header(session),
            reply_markup=topics_keyboard(session.topics, 0),
        )
        return

    if data == "newtopic":
        session.state = State.ASK_NEW_TOPIC_TITLE
        await cq.answer()
        await cq.message.edit_text(
            f"✅ Source: **{session.source_title}**\n"
            f"✅ Destination: **{session.dest_title}**\n\n"
            "➕ **New topic**\n\nSend the title for the new topic.\n\nSend /cancel to abort."
        )
        return

    if data.startswith("topic:"):
        topic_id = int(data.split(":", 1)[1])
        topic = next((t for t in session.topics if t.id == topic_id), None)
        if topic is None:
            await cq.answer("That topic is gone — refresh.", show_alert=True)
            return
        session.chosen_topic = topic
        session.state = State.CONFIRM
        await cq.answer()
        await _show_confirm_edit(ctx, cq, session)
        return

    await cq.answer()


async def _callback_confirm(
    ctx: AppContext, bot: Client, cq: CallbackQuery, session: Session
) -> None:
    data = cq.data or ""

    if data == "confirm:back":
        session.state = State.CHOOSING_TOPIC
        await cq.answer()
        await cq.message.edit_text(
            _topics_header(session),
            reply_markup=topics_keyboard(session.topics, session.topic_page),
        )
        return

    if data == "confirm:cancel":
        session.reset_flow()
        await cq.answer("Cancelled")
        await cq.message.edit_text("❌ Cancelled. Send /copy to start again.")
        return

    if data == "confirm:copy":
        await _start_copy(ctx, bot, cq, session, resume=False)
        return

    if data == "confirm:resume":
        await _start_copy(ctx, bot, cq, session, resume=True)
        return

    if data == "confirm:restart":
        if session.source_id is not None and session.dest_id is not None and session.chosen_topic:
            clear_checkpoint(
                ctx.cfg.sessions_dir, session.source_id, session.dest_id, session.chosen_topic.id
            )
        await _start_copy(ctx, bot, cq, session, resume=False)
        return

    await cq.answer()


def _checkpoint(ctx: AppContext, session: Session):
    if session.source_id is None or session.dest_id is None or session.chosen_topic is None:
        return None
    return load_checkpoint(
        ctx.cfg.sessions_dir, session.source_id, session.dest_id, session.chosen_topic.id
    )


def _confirm_text(session: Session, ckpt: dict | None = None) -> str:
    topic = session.chosen_topic
    base = (
        "🧾 **Confirm copy**\n\n"
        f"📥 From: **{session.source_title}**\n"
        f"📤 To: **{session.dest_title}**\n"
        f"📌 Topic: **{topic.title if topic else '?'}**\n\n"
    )
    if ckpt:
        return base + (
            f"♻️ An unfinished copy was found: **{ckpt['done']}/{ckpt['total']}** already done.\n\n"
            "**▶️ Resume** continues without duplicates.\n"
            "**🔁 Start over** copies everything again from the beginning."
        )
    return base + (
        "All messages from the source will be copied into this topic, in order.\n\n"
        "Press **✅ Copy** to begin."
    )


async def _show_confirm_edit(ctx: AppContext, cq: CallbackQuery, session: Session) -> None:
    ckpt = _checkpoint(ctx, session)
    await cq.message.edit_text(_confirm_text(session, ckpt), reply_markup=confirm_keyboard(bool(ckpt)))


async def _start_copy(
    ctx: AppContext, bot: Client, cq: CallbackQuery, session: Session, *, resume: bool
) -> None:
    if session.chosen_topic is None or session.source_id is None or session.dest_id is None:
        await cq.answer("Missing selection — start again with /copy.", show_alert=True)
        session.reset_flow()
        return

    source_id = session.source_id
    dest_id = session.dest_id
    topic = session.chosen_topic
    session.state = State.COPYING

    await cq.answer("Resuming…" if resume else "Starting…")
    header = (
        "🚀 **Copying**\n"
        f"📥 {session.source_title} → 📤 {session.dest_title} / 📌 {topic.title}"
    )
    try:
        await cq.message.edit_text(header)
    except Exception:  # noqa: BLE001
        pass

    reporter = ProgressReporter(
        client=bot,
        chat_id=cq.message.chat.id,
        message_id=cq.message.id,
        header=header,
        interval=ctx.cfg.progress_edit_interval,
    )

    try:
        await run_copy(
            ctx.userbot.client,
            source_id=source_id,
            dest_id=dest_id,
            topic_id=topic.id,
            reporter=reporter,
            copy_delay=ctx.cfg.copy_delay,
            ckpt_dir=ctx.cfg.sessions_dir,
            resume=resume,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("copy failed")
        await reporter.fail(f"`{exc}`")
    finally:
        session.reset_flow()
