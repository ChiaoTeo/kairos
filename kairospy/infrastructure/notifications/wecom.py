from __future__ import annotations

from typing import Any

from kairospy.application.usecases.notification import NotificationRequest, NotificationSender
from kairospy.application.usecases.notification.application import notification_body

from .transport import HttpNotificationTransport, NotificationTransportError


class WeComNotificationSender(NotificationSender):
    """Send Markdown notifications through a WeCom bot webhook."""

    def __init__(self, webhook_url: str, *, transport: HttpNotificationTransport | None = None) -> None:
        self.webhook_url = webhook_url
        self.transport = transport or HttpNotificationTransport()

    @property
    def channel(self) -> str:
        return "wecom"

    async def send(self, request: NotificationRequest) -> None:
        payload: dict[str, Any] = {
            "msgtype": "markdown",
            "markdown": {"content": f"# {request.title}\n{request.body}"},
        }
        response = await self.transport.post_json(self.webhook_url, payload)
        if response.get("errcode", 0) not in (0, None):
            raise NotificationTransportError(f"wecom rejected notification: errcode={response.get('errcode')}", retryable=False)


__all__ = ["WeComNotificationSender"]
