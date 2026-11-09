"""Topic-Creator — entry point.

A single process runs two Pyrogram/Kurigram clients:
  * a **bot** client (your control panel; you chat with it)
  * a **userbot** client (your account, logged in through the bot) that does the
    actual reading and copying.
"""
from __future__ import annotations

import asyncio
import logging

from pyrogram import Client, idle

from config import Config
from core.app_context import AppContext
from core.states import SessionStore
from core.userbot import UserBot
from handlers.router import register

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
# Pyrogram is chatty at INFO; keep our own logs readable.
logging.getLogger("pyrogram").setLevel(logging.WARNING)
log = logging.getLogger("topic-creator")


async def main() -> None:
    cfg = Config.load()
    store = SessionStore()
    userbot = UserBot(cfg)
    ctx = AppContext(cfg=cfg, store=store, userbot=userbot)

    bot = Client(
        name="control-bot",
        api_id=cfg.api_id,
        api_hash=cfg.api_hash,
        bot_token=cfg.bot_token,
        in_memory=True,
    )
    register(bot, ctx)

    await bot.start()
    me = await bot.get_me()
    log.info("Control bot online: @%s", me.username)

    if await userbot.restore():
        log.info("Userbot session restored — ready to copy.")
    else:
        log.info("No userbot session yet — owner must /login.")

    log.info("Owner id allowed: %s", cfg.owner_id)
    await idle()

    log.info("Shutting down…")
    await userbot.shutdown()
    await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
