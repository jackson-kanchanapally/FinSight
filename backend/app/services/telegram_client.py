"""Small Telegram Bot API transport; credentials never enter errors or logs."""
import logging
import re

import httpx

from app.config import settings


class _RedactBotToken(logging.Filter):
    def filter(self, record):
        record.msg = re.sub(r"/bot[^/\s\"?]+/", "/bot[REDACTED]/", record.getMessage())
        record.args = ()
        return True


# httpx logs request URLs at INFO; Telegram puts its credential in the path.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).addFilter(_RedactBotToken())


class TelegramError(Exception):
    def __init__(self, reason: str, *, retryable=True, retry_after=None, uncertain=False):
        super().__init__(reason)
        self.retryable = retryable
        self.retry_after = retry_after
        self.uncertain = uncertain


def configured() -> bool:
    return bool(settings.telegram_bot_token.get_secret_value().strip()
                and settings.telegram_chat_id.strip())


async def call_bot(method: str, payload: dict | None = None, *, transport=None):
    if method not in {"getMe", "getUpdates", "sendMessage"}:
        raise ValueError("Unsupported Telegram method")
    token = settings.telegram_bot_token.get_secret_value().strip()
    if not token:
        raise TelegramError("Telegram bot token is missing", retryable=False)
    try:
        async with httpx.AsyncClient(timeout=settings.telegram_timeout_seconds,
                                     follow_redirects=False, transport=transport) as client:
            response = await client.post(f"https://api.telegram.org/bot{token}/{method}",
                                         json=payload or {})
        try:
            data = response.json()
        except ValueError:
            raise TelegramError("Telegram returned an invalid response", uncertain=method == "sendMessage") from None
        if not isinstance(data, dict):
            raise TelegramError("Telegram returned an invalid response", uncertain=method == "sendMessage")
        if response.status_code != 200 or data.get("ok") is not True:
            code = data.get("error_code", response.status_code)
            if not isinstance(code, int):
                code = response.status_code
            parameters = data.get("parameters") or {}
            delay = parameters.get("retry_after") if isinstance(parameters, dict) else None
            delay = delay if isinstance(delay, int) and delay > 0 else None
            raise TelegramError(f"Telegram API error {code}",
                                retryable=code == 429 or response.status_code >= 500,
                                retry_after=delay)
        return data.get("result")
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
        raise TelegramError("Telegram connection failed before delivery") from None
    except httpx.HTTPError:
        raise TelegramError("Telegram delivery outcome is unknown" if method == "sendMessage" else "Telegram connection failed",
                            uncertain=method == "sendMessage") from None


async def send_message(chat_id: str, message: str, *, transport=None, parse_mode=None) -> int:
    payload = {
        "chat_id": chat_id,
        "text": message,
        "link_preview_options": {"is_disabled": True},
    }
    if parse_mode is not None:
        if parse_mode != "HTML":
            raise ValueError("Unsupported Telegram parse mode")
        payload["parse_mode"] = parse_mode
    result = await call_bot("sendMessage", payload, transport=transport)
    if not isinstance(result, dict) or not isinstance(result.get("message_id"), int):
        raise TelegramError("Telegram returned an invalid message receipt", uncertain=True)
    return result["message_id"]
