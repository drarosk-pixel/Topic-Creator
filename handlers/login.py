"""Login conversation: phone -> OTP -> optional 2FA password.

All functions here are plain (undecorated); the router dispatches to them based
on the owner's current FSM state so the sequence can never be skipped.
"""
from __future__ import annotations

import logging

from pyrogram import Client
from pyrogram.types import Message

from core.app_context import AppContext
from core.parsing import strip_code
from core.states import Session, State
from core.userbot import LoginResult

log = logging.getLogger(__name__)

_CODE_HINT = (
    "⚠️ **Important:** Telegram cancels a login code if you send the plain "
    "number in a chat. Send it with spaces or dashes between the digits, e.g. "
    "`1 2 3 4 5` or `1-2-3-4-5`."
)


async def cmd_login(ctx: AppContext, bot: Client, message: Message, session: Session) -> None:
    if ctx.userbot.is_logged_in:
        await message.reply(
            "✅ The userbot is already logged in.\n"
            "Use /copy to start, or /logout to switch account."
        )
        return
    session.reset_login()
    session.state = State.LOGIN_PHONE
    await message.reply(
        "🔐 **Userbot login**\n\n"
        "Send your phone number in international format, including the country "
        "code.\n\nExample: `+14155550123`\n\n"
        "Send /cancel to abort."
    )


async def on_text(ctx: AppContext, bot: Client, message: Message, session: Session) -> None:
    if session.state is State.LOGIN_PHONE:
        await _handle_phone(ctx, message, session)
    elif session.state is State.LOGIN_CODE:
        await _handle_code(ctx, message, session)
    elif session.state is State.LOGIN_PASSWORD:
        await _handle_password(ctx, message, session)


async def _handle_phone(ctx: AppContext, message: Message, session: Session) -> None:
    phone = (message.text or "").strip()
    if not phone.lstrip("+").isdigit():
        await message.reply("❌ That doesn't look like a phone number. Try again, e.g. `+14155550123`.")
        return
    await message.reply("📲 Requesting a login code from Telegram…")
    try:
        result = await ctx.userbot.begin_login(phone)
    except Exception as exc:  # noqa: BLE001
        log.exception("begin_login failed")
        session.state = State.IDLE
        await message.reply(f"❌ Could not start login: `{exc}`\n\nSend /login to try again.")
        return

    if result is LoginResult.PHONE_INVALID:
        await message.reply("❌ Telegram rejected that phone number. Send a valid one, or /cancel.")
        return

    session.phone = phone
    session.state = State.LOGIN_CODE
    await message.reply(
        "✉️ A login code was sent to your Telegram app.\n\n"
        f"{_CODE_HINT}\n\nSend /cancel to abort."
    )


async def _handle_code(ctx: AppContext, message: Message, session: Session) -> None:
    code = strip_code(message.text or "")
    if not code:
        await message.reply(f"❌ No digits found.\n\n{_CODE_HINT}")
        return
    try:
        result = await ctx.userbot.submit_code(code)
    except Exception as exc:  # noqa: BLE001
        log.exception("submit_code failed")
        session.state = State.IDLE
        await message.reply(f"❌ Login error: `{exc}`\n\nSend /login to try again.")
        return

    if result is LoginResult.NEEDS_PASSWORD:
        session.state = State.LOGIN_PASSWORD
        await message.reply(
            "🔑 Your account has two-step verification enabled.\n"
            "Send your **cloud password** now.\n\nSend /cancel to abort."
        )
    elif result is LoginResult.CODE_INVALID:
        await message.reply(f"❌ That code is invalid. Try again.\n\n{_CODE_HINT}")
    elif result is LoginResult.CODE_EXPIRED:
        session.state = State.IDLE
        await message.reply("❌ The code expired. Send /login to request a new one.")
    elif result is LoginResult.OK:
        await _login_success(ctx, message, session)


async def _handle_password(ctx: AppContext, message: Message, session: Session) -> None:
    password = (message.text or "").strip()
    if not password:
        await message.reply("❌ Please send your 2FA password, or /cancel.")
        return
    try:
        await ctx.userbot.submit_password(password)
    except Exception as exc:  # noqa: BLE001
        log.exception("submit_password failed")
        await message.reply(f"❌ Password check failed: `{exc}`\n\nSend it again, or /cancel.")
        return
    await _login_success(ctx, message, session)


async def _login_success(ctx: AppContext, message: Message, session: Session) -> None:
    session.reset_login()
    session.state = State.IDLE
    try:
        me = await ctx.userbot.client.get_me()
        who = f"**{me.first_name}**" + (f" (@{me.username})" if me.username else "")
    except Exception:  # noqa: BLE001
        who = "your account"
    await message.reply(
        f"✅ Logged in as {who}.\n\n"
        "Now send /copy to start copying a channel into a topic."
    )


async def cmd_logout(ctx: AppContext, bot: Client, message: Message, session: Session) -> None:
    if not ctx.userbot.is_logged_in:
        await message.reply("ℹ️ The userbot isn't logged in.")
        return
    await ctx.userbot.logout()
    session.reset_flow()
    session.reset_login()
    await message.reply("👋 Logged out and session removed. Send /login to sign in again.")
