from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any

from kairospy.application.usecases.notification import NotificationRequest, NotificationSender
from kairospy.application.usecases.notification.application import notification_body

from .transport import HttpNotificationTransport, NotificationTransportError


class FeishuNotificationSender(NotificationSender):
    """Send text notifications through a Feishu custom bot webhook."""

    def __init__(self, webhook_url: str, *, secret: str | None = None, transport: HttpNotificationTransport | None = None) -> None:
        self.webhook_url = webhook_url
        self.secret = secret
        self.transport = transport or HttpNotificationTransport()

    @property
    def channel(self) -> str:
        return "feishu"

    async def send(self, request: NotificationRequest) -> None:
        timestamp = str(int(time.time()))
        payload: dict[str, Any] = {
            "msg_type": "text",
            "content": {"text": notification_body(request.title, request.body)},
        }
        if self.secret:
            payload["timestamp"] = timestamp
            payload["sign"] = self._signature(timestamp)
        response = await self.transport.post_json(self.webhook_url, payload)
        if response.get("code", 0) not in (0, None):
            raise NotificationTransportError(f"feishu rejected notification: code={response.get('code')}", retryable=False)

    def _signature(self, timestamp: str) -> str:
        digest = hmac.new(
            self.secret.encode("utf-8"),
            f"{timestamp}\n{self.secret}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("ascii")


__all__ = ["FeishuNotificationSender"]
