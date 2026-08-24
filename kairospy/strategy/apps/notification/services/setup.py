from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Mapping
from urllib.parse import quote
from urllib.request import Request, urlopen


@dataclass(frozen=True, slots=True)
class TelegramBotIdentity:
    bot_id: int
    username: str
    display_name: str


@dataclass(frozen=True, slots=True)
class TelegramChat:
    chat_id: str
    kind: str
    title: str


class TelegramSetupClient:
    """Small setup-only Telegram Bot API client used for credential probing."""

    def __init__(self, bot_token: str, *, timeout_seconds: float = 10) -> None:
        self._token = bot_token
        self._timeout = timeout_seconds

    def identity(self) -> TelegramBotIdentity:
        result = self._call("getMe")
        if not isinstance(result, Mapping):
            raise ValueError("Telegram getMe probe returned an invalid result")
        return TelegramBotIdentity(
            bot_id=int(result.get("id", 0)),
            username=str(result.get("username", "")),
            display_name=" ".join(
                item
                for item in (
                    str(result.get("first_name", "")).strip(),
                    str(result.get("last_name", "")).strip(),
                )
                if item
            ),
        )

    def chats(self) -> tuple[TelegramChat, ...]:
        updates = self._call("getUpdates")
        if not isinstance(updates, list):
            return ()
        chats: dict[str, TelegramChat] = {}
        for update in updates:
            if not isinstance(update, Mapping):
                continue
            message = next(
                (
                    value
                    for key in ("message", "channel_post", "edited_message")
                    if isinstance((value := update.get(key)), Mapping)
                ),
                None,
            )
            chat = message.get("chat") if isinstance(message, Mapping) else None
            if not isinstance(chat, Mapping) or chat.get("id") is None:
                continue
            chat_id = str(chat["id"])
            title = str(
                chat.get("title")
                or chat.get("username")
                or " ".join(
                    item
                    for item in (
                        str(chat.get("first_name", "")).strip(),
                        str(chat.get("last_name", "")).strip(),
                    )
                    if item
                )
                or chat_id
            )
            chats[chat_id] = TelegramChat(
                chat_id=chat_id,
                kind=str(chat.get("type", "unknown")),
                title=title,
            )
        return tuple(chats.values())

    def _call(self, method: str) -> object:
        url = f"https://api.telegram.org/bot{quote(self._token, safe=':')}/{method}"
        request = Request(url, headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as error:
            raise ValueError(f"Telegram {method} probe failed") from error
        if not isinstance(payload, Mapping) or payload.get("ok") is not True:
            description = (
                str(payload.get("description", "request rejected"))
                if isinstance(payload, Mapping)
                else "invalid response"
            )
            raise ValueError(f"Telegram {method} probe failed: {description}")
        return payload.get("result")


__all__ = ["TelegramBotIdentity", "TelegramChat", "TelegramSetupClient"]
