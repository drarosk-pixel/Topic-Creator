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
| `/status` | Show current step and selections |
| `/cancel` | Abort the current step |

## Security notes

- Only `OWNER_ID` can control the bot; everyone else is refused.
- The userbot session string is stored locally in `sessions/` (git-ignored) with
  `600` permissions. Treat it like a password — anyone with it can act as your
  account. `/logout` removes it.
- Never commit your `.env` or the `sessions/` directory.

## Notes & limits

- A userbot copying large channels can hit Telegram rate limits; the bot waits
  out `FloodWait` automatically. Increase `COPY_DELAY` if you copy very large
  channels and want to be gentler.
- Uses [Kurigram](https://github.com/kurigram-org/kurigram), a maintained
  Pyrogram fork with full forum/topic support.
