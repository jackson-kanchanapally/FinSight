"""Connect a personal Telegram bot; store its token locally with hidden input."""
import argparse
import asyncio
from getpass import getpass
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from pydantic import SecretStr
from app.config import settings
from app.services.telegram_client import TelegramError, call_bot, send_message


def write_settings(path, values):
    lines = path.read_text().splitlines() if path.exists() else []
    keys = set(values)
    lines = [line for line in lines if line.partition("=")[0].strip() not in keys]
    lines.extend(f"{key}={value}" for key, value in values.items())
    fd, temporary = tempfile.mkstemp(prefix=".telegram-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            output.write("\n".join(lines) + "\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


async def setup(send_test=False):
    token = getpass("BotFather token (hidden): ").strip()
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise ValueError("That does not look like a BotFather token")
    settings.telegram_bot_token = SecretStr(token)
    bot = await call_bot("getMe")
    if not isinstance(bot, dict) or not bot.get("is_bot"):
        raise ValueError("Telegram did not identify a bot")
    print(f"Bot verified: @{bot['username']}")
    input(f"Open https://t.me/{bot['username']}, send /start, then press Enter here: ")
    updates = await call_bot("getUpdates", {"timeout": 0, "limit": 100})
    chats = {}
    for update in updates or []:
        message = update.get("message", {})
        chat = message.get("chat", {})
        if chat.get("type") == "private" and not message.get("from", {}).get("is_bot"):
            chats[str(chat["id"])] = chat
    if not chats:
        raise ValueError("No private chat found. Send /start to this bot, then run setup again. "
                         "Use a dedicated bot without an existing webhook.")
    choices = list(chats.items())
    for index, (chat_id, chat) in enumerate(choices, 1):
        print(f"{index}. {chat.get('first_name', 'Private chat')} "
              f"(@{chat.get('username', 'no username')}) · chat {chat_id}")
    selected = int(input("Choose your destination number: ")) - 1
    if selected < 0 or selected >= len(choices):
        raise ValueError("Invalid destination number")
    chat_id = choices[selected][0]
    write_settings(ROOT / ".env", {"TELEGRAM_ENABLED": "true", "TELEGRAM_BOT_TOKEN": token,
                                   "TELEGRAM_CHAT_ID": chat_id})
    print("Saved Telegram settings in the project's private .env file.")
    if send_test:
        await send_message(chat_id, "Almonds is connected. Portfolio event alerts with short AI theses are ready.")
        print("Test message sent.")
    print("Restart Almonds to load these settings. For the installed login service, follow docs/telegram.md.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true", help="Send one connection test to the selected chat")
    args = parser.parse_args()
    try:
        asyncio.run(setup(args.test))
    except (TelegramError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    except (KeyboardInterrupt, EOFError):
        raise SystemExit("Setup cancelled") from None
