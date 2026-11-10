"""Registers all bot handlers and dispatches messages/callbacks by FSM state.

Everything lives in handler group 0 and command handlers are registered before
the catch-all handlers, so Pyrogram runs exactly one handler per update and the
strict step sequence is preserved.
"""
from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message

from core.app_context import AppContext
from core.states import State
from handlers import flow, login, settings as settings_handler

log = logging.getLogger(__name__)

_LOGIN_TEXT_STATES = {State.LOGIN_PHONE, State.LOGIN_CODE, State.LOGIN_PASSWORD}
_FLOW_TEXT_STATES = {State.ASK_SOURCE, State.ASK_DEST, State.ASK_NEW_TOPIC_TITLE}
_SETTINGS_TEXT_STATES = {
    State.SET_WORD_REPLACE,
    State.SET_LINK_REPLACE,
    State.SET_CAPTION_TEXT,
}


def register(bot: Client, ctx: AppContext) -> None:
    owner = filters.private & filters.user(ctx.cfg.owner_id)

    # ----------------------------- commands ----------------------------- #
    @bot.on_message(owner & filters.command("start"))
    async def _start(_: Client, message: Message) -> None:
        session = ctx.store.get(message.from_user.id)
        session.reset_flow()
        status = "✅ logged in" if ctx.userbot.is_logged_in else "🔒 not logged in"
        await message.reply(
            "👋 **Topic-Creator** — channel → topic copier.\n\n"
            f"Userbot status: {status}\n\n"
            "**Commands**\n"
            "• /login — sign the userbot in (phone + code)\n"
            "• /copy — copy a channel into a topic\n"
            "• /settings — word/link replace, caption\n"
            "• /status — show current status\n"
            "• /logout — sign the userbot out\n"
            "• /cancel — abort the current step\n\n"
            + ("Send /copy to begin." if ctx.userbot.is_logged_in else "Start with /login.")
        )

    @bot.on_message(owner & filters.command("help"))
    async def _help(_: Client, message: Message) -> None:
        await _start(_, message)

    @bot.on_message(owner & filters.command("status"))
    async def _status(_: Client, message: Message) -> None:
        session = ctx.store.get(message.from_user.id)
        lines = [
            f"Userbot: {'✅ logged in' if ctx.userbot.is_logged_in else '🔒 not logged in'}",
            f"Current step: `{session.state.name}`",
        ]
        if session.source_title:
            lines.append(f"Source: {session.source_title}")
        if session.dest_title:
            lines.append(f"Destination: {session.dest_title}")
        if session.chosen_topic:
            lines.append(f"Topic: {session.chosen_topic.title}")
        await message.reply("\n".join(lines))

    @bot.on_message(owner & filters.command("cancel"))
    async def _cancel(_: Client, message: Message) -> None:
        session = ctx.store.get(message.from_user.id)
        if session.state is State.COPYING:
            await message.reply("⏳ A copy is running; let it finish (or restart the bot to stop).")
            return
        session.reset_flow()
        session.reset_login()
        await message.reply("🛑 Cancelled. Send /start to see options.")

    @bot.on_message(owner & filters.command("login"))
    async def _login(_: Client, message: Message) -> None:
        session = ctx.store.get(message.from_user.id)
        await login.cmd_login(ctx, bot, message, session)

    @bot.on_message(owner & filters.command("logout"))
    async def _logout(_: Client, message: Message) -> None:
        session = ctx.store.get(message.from_user.id)
        await login.cmd_logout(ctx, bot, message, session)

    @bot.on_message(owner & filters.command("copy"))
    async def _copy(_: Client, message: Message) -> None:
        session = ctx.store.get(message.from_user.id)
        await flow.cmd_copy(ctx, bot, message, session)

    @bot.on_message(owner & filters.command("settings"))
    async def _settings(_: Client, message: Message) -> None:
        session = ctx.store.get(message.from_user.id)
        await settings_handler.cmd_settings(ctx, bot, message, session)

    # ----------------------- catch-all text router ---------------------- #
    @bot.on_message(owner & filters.text)
    async def _text(_: Client, message: Message) -> None:
        text = message.text or ""
        if text.startswith("/"):
            return  # a command we don't recognise; ignore quietly
        session = ctx.store.get(message.from_user.id)
        if session.state in _LOGIN_TEXT_STATES:
            await login.on_text(ctx, bot, message, session)
        elif session.state in _FLOW_TEXT_STATES:
            await flow.on_text(ctx, bot, message, session)
        elif session.state in _SETTINGS_TEXT_STATES:
            await settings_handler.on_text(ctx, bot, message, session)
        else:
            await message.reply("ℹ️ Send /copy to start, /settings to configure, or /start for the menu.")

    # --------------------------- callbacks ------------------------------ #
    @bot.on_callback_query(filters.user(ctx.cfg.owner_id))
    async def _callback(_: Client, cq: CallbackQuery) -> None:
        session = ctx.store.get(cq.from_user.id)
        data = cq.data or ""
        try:
            if data.startswith("set:") or data.startswith("capmode:"):
                await settings_handler.on_callback(ctx, bot, cq, session)
            else:
                await flow.on_callback(ctx, bot, cq, session)
        except Exception:  # noqa: BLE001
            log.exception("callback handler failed")
            try:
                await cq.answer("Something went wrong.", show_alert=True)
            except Exception:  # noqa: BLE001
                pass

    # deny everyone who isn't the owner, with a short note
    @bot.on_message(filters.private & ~filters.user(ctx.cfg.owner_id))
    async def _deny(_: Client, message: Message) -> None:
        await message.reply("⛔ This is a private bot.")
