"""The /settings flow: configure copy transformations via an inline menu.

Features: word replace, hyperlink replace, caption (append/prepend/replace),
and a custom thumbnail. Settings are global for the owner and persist to disk.
"""
from __future__ import annotations

import logging

from pyrogram import Client
from pyrogram.types import CallbackQuery, Message

from core.app_context import AppContext
from core.parsing import parse_pairs
from core.settings import summary
from core.states import Session, State
from handlers.keyboards import caption_mode_keyboard, settings_menu_keyboard

log = logging.getLogger(__name__)

_MENU_HEADER = "⚙️ **Copy settings**\n\nApplied to every message while copying.\n\n"


# ------------------------------------------------------------------ #
# Entry + menu
# ------------------------------------------------------------------ #
async def cmd_settings(ctx: AppContext, bot: Client, message: Message, session: Session) -> None:
    session.state = State.IDLE
    session.pending_caption = None
    await message.reply(
        _MENU_HEADER + summary(ctx.settings), reply_markup=settings_menu_keyboard()
    )


async def _show_menu_edit(ctx: AppContext, cq: CallbackQuery) -> None:
    await cq.message.edit_text(
        _MENU_HEADER + summary(ctx.settings), reply_markup=settings_menu_keyboard()
    )


# ------------------------------------------------------------------ #
# Callbacks: set:* and capmode:*
# ------------------------------------------------------------------ #
async def on_callback(ctx: AppContext, bot: Client, cq: CallbackQuery, session: Session) -> None:
    data = cq.data or ""

    if data == "set:close":
        session.state = State.IDLE
        await cq.answer("Closed")
        await cq.message.edit_text("⚙️ Settings closed. Send /settings to reopen.")
        return

    if data == "set:clearall":
        ctx.settings.word_replace = []
        ctx.settings.link_replace = []
        ctx.settings.caption_text = ""
        ctx.settings.caption_mode = "off"
        ctx.settings.thumbnail_path = None
        ctx.settings.save(ctx.cfg.settings_file)
        await cq.answer("All settings cleared")
        await _show_menu_edit(ctx, cq)
        return

    if data == "set:word":
        session.state = State.SET_WORD_REPLACE
        await cq.answer()
        await cq.message.edit_text(
            "✏️ **Word replace**\n\n"
            "Send replacement rules, one per line:\n"
            "`old text => new text`\n\n"
            "• A line without `=>` deletes that text.\n"
            "• This **replaces** the whole list.\n"
            "• Send `clear` to remove all rules.\n\n"
            "Send /cancel to go back."
        )
        return

    if data == "set:link":
        session.state = State.SET_LINK_REPLACE
        await cq.answer()
        await cq.message.edit_text(
            "🔗 **Link replace**\n\n"
            "Send link rules, one per line:\n"
            "`https://old.link => https://new.link`\n\n"
            "• Matches links in text and captions.\n"
            "• This **replaces** the whole list.\n"
            "• Send `clear` to remove all rules.\n\n"
            "Send /cancel to go back."
        )
        return

    if data == "set:caption":
        session.state = State.SET_CAPTION_TEXT
        await cq.answer()
        await cq.message.edit_text(
            "🏷 **Caption**\n\n"
            "Send the caption text to add. You can use HTML like "
            "`<b>bold</b>` or `<a href=\"https://t.me/x\">link</a>`.\n\n"
            "Send `off` to disable the caption.\n\n"
            "Send /cancel to go back."
        )
        return

    if data == "set:thumb":
        session.state = State.SET_THUMBNAIL
        await cq.answer()
        await cq.message.edit_text(
            "🖼 **Thumbnail**\n\n"
            "Send a **photo** to use as the thumbnail for copied videos/documents.\n"
            "⚠️ Thumbnail change makes the bot **download & re-upload** each file, "
            "so it is much slower and heavier.\n\n"
            "Send `clear` to remove the thumbnail.\n\n"
            "Send /cancel to go back."
        )
        return

    if data.startswith("capmode:"):
        mode = data.split(":", 1)[1]
        if mode == "off":
            ctx.settings.caption_mode = "off"
            ctx.settings.caption_text = ""
        else:
            ctx.settings.caption_mode = mode
            ctx.settings.caption_text = session.pending_caption or ctx.settings.caption_text
        session.pending_caption = None
        session.state = State.IDLE
        ctx.settings.save(ctx.cfg.settings_file)
        await cq.answer("Caption updated")
        await _show_menu_edit(ctx, cq)
        return

    await cq.answer()


# ------------------------------------------------------------------ #
# Text inputs for the sub-flows
# ------------------------------------------------------------------ #
async def on_text(ctx: AppContext, bot: Client, message: Message, session: Session) -> None:
    text = message.text or ""
    if session.state is State.SET_WORD_REPLACE:
        if text.strip().lower() == "clear":
            ctx.settings.word_replace = []
        else:
            ctx.settings.word_replace = parse_pairs(text)
        session.state = State.IDLE
        ctx.settings.save(ctx.cfg.settings_file)
        await message.reply(
            f"✅ Word replace saved ({len(ctx.settings.word_replace)} rule(s)).\n\n"
            + _MENU_HEADER + summary(ctx.settings),
            reply_markup=settings_menu_keyboard(),
        )
        return

    if session.state is State.SET_LINK_REPLACE:
        if text.strip().lower() == "clear":
            ctx.settings.link_replace = []
        else:
            ctx.settings.link_replace = parse_pairs(text)
        session.state = State.IDLE
        ctx.settings.save(ctx.cfg.settings_file)
        await message.reply(
            f"✅ Link replace saved ({len(ctx.settings.link_replace)} rule(s)).\n\n"
            + _MENU_HEADER + summary(ctx.settings),
            reply_markup=settings_menu_keyboard(),
        )
        return

    if session.state is State.SET_CAPTION_TEXT:
        if text.strip().lower() == "off":
            ctx.settings.caption_mode = "off"
            ctx.settings.caption_text = ""
            session.state = State.IDLE
            ctx.settings.save(ctx.cfg.settings_file)
            await message.reply("✅ Caption disabled.", reply_markup=settings_menu_keyboard())
            return
        session.pending_caption = text
        await message.reply(
            "How should this caption be applied to each message?",
            reply_markup=caption_mode_keyboard(),
        )
        return


# ------------------------------------------------------------------ #
# Thumbnail photo input
# ------------------------------------------------------------------ #
async def on_thumbnail_photo(
    ctx: AppContext, bot: Client, message: Message, session: Session
) -> None:
    notice = await message.reply("🖼 Preparing thumbnail…")
    try:
        raw_path = ctx.cfg.sessions_dir / "thumb_raw"
        await bot.download_media(message, file_name=str(raw_path))
        _prepare_thumbnail(str(raw_path), str(ctx.cfg.thumbnail_file))
        ctx.settings.thumbnail_path = str(ctx.cfg.thumbnail_file)
        session.state = State.IDLE
        ctx.settings.save(ctx.cfg.settings_file)
        await notice.edit_text("✅ Thumbnail set.")
        await message.reply(_MENU_HEADER + summary(ctx.settings), reply_markup=settings_menu_keyboard())
    except Exception as exc:  # noqa: BLE001
        log.exception("thumbnail prepare failed")
        await notice.edit_text(f"❌ Could not set thumbnail: `{exc}`")


def _prepare_thumbnail(src: str, dst: str) -> None:
    """Resize/convert an image into a valid Telegram thumbnail (<=320px JPEG)."""
    from PIL import Image

    with Image.open(src) as img:
        img = img.convert("RGB")
        img.thumbnail((320, 320))
        img.save(dst, format="JPEG", quality=80, optimize=True)
