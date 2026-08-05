from __future__ import annotations

from typing import Any

from kairospy.application.usecases.notification import NotificationRequest, NotificationSender
from kairospy.application.usecases.notification.application import notification_body

from .transport import HttpNotificationTransport, NotificationTransportError


class TelegramNotificationSender(NotificationSender):
    """Send outbound messages through the Telegram Bot API."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        parse_mode: str | None = None,
        transport: HttpNotificationTransport | None = None,
    ) -> None:
        if not bot_token.strip():
            raise ValueError("telegram bot token is required")
        if not str(chat_id).strip():
            raise ValueError("telegram chat id is required")
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.parse_mode = parse_mode
        self.transport = transport or HttpNotificationTransport()

    @property
    def channel(self) -> str:
        return "telegram"

    async def send(self, request: NotificationRequest) -> None:
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": self._text(request),
        }
        if self.parse_mode:
            payload["parse_mode"] = self.parse_mode
        response = await self.transport.post_json(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            payload,
        )
        if response.get("ok") is not True:
            description = response.get("description", "unknown Telegram error")
            raise NotificationTransportError(f"telegram rejected notification: {description}", retryable=False)

    def _text(self, request: NotificationRequest) -> str:
        content = notification_body(request.title, request.body)
        if self.parse_mode == "MarkdownV2":
            return _escape_markdown_v2(content)
        return content


def _escape_markdown_v2(value: str) -> str:
    reserved = r"_[]()~`>#+-=|{}.!"
    return "".join(f"\\{char}" if char in reserved else char for char in value)


__all__ = ["TelegramNotificationSender"]
