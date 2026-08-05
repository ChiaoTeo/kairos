"""Consumer-owned contracts for notification delivery."""

from __future__ import annotations

from typing import Protocol

from .domain import NotificationRequest


class NotificationSender(Protocol):
    """Minimal channel contract consumed by the notification application."""

    @property
    def channel(self) -> str:
        ...

    async def send(self, request: NotificationRequest) -> None:
        ...


__all__ = ["NotificationSender"]
