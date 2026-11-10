# Topic-Creator

A Telegram **userbot controlled through a bot** that copies all content from a
source channel into a chosen **topic** of a destination topic-group (forum).

You log the userbot in directly through the bot with your phone number and the
Telegram login code — **no session string to paste**. The flow is strictly
sequential (nothing can be skipped), and a live **progress bar** shows copy
speed and ETA.

## How it works

Two clients run in one process:

- **Control bot** — the chat interface you talk to (`BOT_TOKEN`).
- **Userbot** — your own account, logged in via the bot, which reads the source
  and copies messages into the destination topic.

### The flow

```
/login  ─▶  phone number  ─▶  login code  ─▶  (2FA password if enabled)  ─▶  ✅ logged in
/copy   ─▶  source channel  ─▶  destination topic-group  ─▶  pick a topic  ─▶  Confirm
            └─ buttons: ✅ Copy  /  🔙 Back  /  ❌ Cancel ─┘
```

Albums are kept together, original message order is preserved, service messages
are skipped, and Telegram flood limits are waited out automatically.

## Requirements

- Python 3.10+
- Telegram API credentials from <https://my.telegram.org> (`API_ID`, `API_HASH`)
- A bot token from [@BotFather](https://t.me/BotFather)
- Your numeric user id from [@userinfobot](https://t.me/userinfobot)
- The **userbot account** must be a member of the source channel and a
  member/admin of the destination group (with *Manage Topics* to create topics).
- The destination group must have **Topics** enabled.

## Setup

```bash
git clone https://github.com/drarosk-pixel/Topic-Creator.git
cd Topic-Creator

python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env                 # then edit .env with your values
```

Fill in `.env`:

| Variable  | What it is |
|-----------|------------|
| `API_ID` / `API_HASH` | Your Telegram app credentials (my.telegram.org) |
| `BOT_TOKEN` | Token of the control bot from @BotFather |
| `OWNER_ID` | Your Telegram numeric id — only you can use the bot |

Optional tuning: `PROGRESS_EDIT_INTERVAL`, `COPY_DELAY`, `SESSIONS_DIR`.

## Run

```bash
python app.py
```

Then open your bot in Telegram:

1. `/login` and follow the prompts.
   > ⚠️ When sending the login code, put spaces or dashes between the digits
   > (e.g. `1 2 3 4 5`). Telegram cancels a code if the plain number appears in a
   > chat message.
2. `/copy`, send the **source** channel (ID like `-1001234567890`, @username, or
   t.me link), then the **destination** topic-group.
3. Pick a topic from the buttons (or **➕ New topic** to create one).
4. Press **✅ Copy** and watch the progress bar.

## Commands

| Command | Action |
|---------|--------|
| `/start`, `/help` | Show the menu and status |
| `/login` | Sign the userbot in |
| `/logout` | Sign out and delete the saved session |
| `/copy` | Start the copy flow |
| `/settings` | Configure word/link replace, caption, thumbnail |
| `/status` | Show current step and selections |
| `/cancel` | Abort the current step |

## Transformations (/settings)

Open `/settings` to configure changes applied to **every** message while
copying. They persist until you change them.

- **✏️ Word replace** — find/replace rules (one per line, `old => new`). A line
  without `=>` deletes that text. Applies to message text and captions.
- **🔗 Link replace** — swap links (`https://old => https://new`). Works on links
  inside text and captions.
- **🏷 Caption** — add a caption and choose how: **Append**, **Prepend**, or
  **Replace** the original. Supports HTML (`<b>…</b>`, `<a href="…">…</a>`).
- **🖼 Thumbnail** — send a photo to use as the thumbnail for copied
  videos/documents/animations. ⚠️ This forces a **download + re-upload** of each
  file, so it is much slower and heavier than a normal copy. Albums keep their
  original thumbnails.

Text/caption rewriting (word, link, caption) uses fast reference-copies — no
download. Only the thumbnail feature downloads and re-uploads.

## Security notes

- Only `OWNER_ID` can control the bot; everyone else is refused.
- The userbot session string is stored locally in `sessions/` (git-ignored) with
  `600` permissions. Treat it like a password — anyone with it can act as your
  account. `/logout` removes it.
- Never commit your `.env` or the `sessions/` directory.

## Speed & FloodWait

Telegram rate-limits how fast an account can copy. These limits are server-side
and undocumented, so **no tool can guarantee zero FloodWaits** — copying *too*
fast is exactly what triggers them. Instead, the bot **auto-tunes**:

- It starts at `COPY_DELAY` seconds between copies.
- On a `FloodWait` it backs off (slows down) so the next ones shrink or vanish.
- After a long clean streak it speeds back up a little.

The result settles near the fastest rate your account tolerates. To tune:
lower `COPY_DELAY` for more speed (more FloodWait risk), raise it for a steadier
run. Older/established accounts and Telegram Premium get higher limits; fresh
accounts get throttled hard.

## Resume (no duplicates)

Progress is checkpointed per *(source, destination, topic)*. If a copy is
interrupted — FloodWait give-up, restart, host redeploy — the next time you
reach the confirm screen for the same pair you'll see **▶️ Resume** (continues
where it stopped, no duplicates) and **🔁 Start over**. A completed copy clears
its checkpoint automatically.

> The checkpoint lives in `sessions/`. On hosts with an **ephemeral filesystem**
> it is lost on restart (like the login session) — use a host with a persistent
> disk/volume to keep resume and login working across restarts.

## Notes

- Uses [Kurigram](https://github.com/kurigram-org/kurigram), a maintained
  Pyrogram fork with full forum/topic support.
- Use a **separate/secondary account** for the userbot, not your primary — heavy
  copying can make Telegram temporarily show "connecting" on that account.
- Resolving a source/destination by numeric id can occasionally time out for a
  "cold" peer; the bot now retries a few times automatically. If it still fails,
  make sure the userbot account is a member and try again (or use @username / a
  t.me link).
