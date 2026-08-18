from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Protocol
from urllib.parse import urlsplit

import apprise

from ..models import NotificationDestination, RenderedNotification, SenderResult


class NotificationSender(Protocol):
    async def send(
        self,
        destination: NotificationDestination,
        message: RenderedNotification,
    ) -> SenderResult: ...


def render_text(message: RenderedNotification) -> str:
    identity = " ".join(f"{key}={value}" for key, value in message.identity.items())
    reference = f"notification={message.notification_id[:12]}"
    return (
        f"[{message.severity.upper()}] {message.title}\n\n"
        f"{message.body}\n\n{identity}\n{reference}"
    ).strip()


class AppriseSender:
    """Thin Kairos adapter over Apprise's maintained provider plugins."""

    def __init__(
        self,
        destination: NotificationDestination,
        *,
        request_timeout_seconds: float = 5,
        max_attempts: int = 3,
    ) -> None:
        self.destination_id = destination.destination_id
        asset = apprise.AppriseAsset(
            app_id="Kairos",
            app_desc="Kairos strategy notifications",
            async_mode=True,
            secure_logging=True,
        )
        self._client = apprise.Apprise(asset=asset)
        service = _apprise_service(
            destination,
            request_timeout_seconds=request_timeout_seconds,
            max_attempts=max_attempts,
        )
        try:
            added = self._client.add(service)
        except Exception as error:
            raise ValueError(
                f"Apprise could not configure notification destination "
                f"{destination.destination_id} ({type(error).__name__})"
            ) from error
        if not added:
            raise ValueError(
                f"Apprise rejected notification destination {destination.destination_id}"
            )

    async def send(
        self,
        destination: NotificationDestination,
        message: RenderedNotification,
    ) -> SenderResult:
        if destination.destination_id != self.destination_id:
            return SenderResult("failed", "destination_mismatch")
        result = await self._client.async_notify(
            body=render_text(message),
            title="",
            notify_type=_notify_type(message.severity),
        )
        if result is True:
            return SenderResult("delivered")
        return SenderResult("failed", "apprise_delivery_failed")


class RecordingSender:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def send(
        self,
        destination: NotificationDestination,
        message: RenderedNotification,
    ) -> SenderResult:
        record = {
            "schema_version": 1,
            "notification_id": message.notification_id,
            "destination_id": destination.destination_id,
            "sender": "recording",
            "title": message.title,
            "body": message.body,
            "severity": message.severity,
            "occurred_at": message.occurred_at.isoformat(),
            "attributes": dict(message.attributes),
            "identity": dict(message.identity),
            "outcome": "delivered",
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        return SenderResult("delivered")


def _apprise_service(
    destination: NotificationDestination,
    *,
    request_timeout_seconds: float,
    max_attempts: int,
) -> dict[str, object]:
    common: dict[str, object] = {
        "retry": max(0, max_attempts - 1),
        "rto": request_timeout_seconds,
        "cto": request_timeout_seconds,
        "format": "text",
        "overflow": "upstream",
    }
    if destination.sender == "feishu":
        webhook_url = destination.secrets.get("webhook_url", "")
        token = _feishu_webhook_token(webhook_url)
        return {"schema": "feishu", "token": token, **common}
    if destination.sender == "telegram":
        bot_token = destination.secrets.get("bot_token", "")
        chat_id = destination.settings.get("chat_id", "")
        if not re.fullmatch(r"(?:bot)?[0-9]+:[A-Za-z0-9_-]+", bot_token) or not (
            re.fullmatch(r"-?[0-9]{1,32}", chat_id)
            or re.fullmatch(r"[A-Za-z_-][A-Za-z0-9_-]+", chat_id)
        ):
            raise ValueError(
                f"notification destination {destination.destination_id} has invalid "
                "Telegram credentials"
            )
        return {
            "schema": "tgram",
            "bot_token": bot_token,
            "targets": [chat_id],
            "preview": False,
            **common,
        }
    raise ValueError(
        f"Apprise does not handle notification sender {destination.sender}"
    )


def _feishu_webhook_token(webhook_url: str) -> str:
    parsed = urlsplit(webhook_url)
    parts = tuple(part for part in parsed.path.split("/") if part)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "open.feishu.cn"
        or len(parts) < 2
        or parts[-2] != "hook"
        or not parts[-1]
    ):
        raise ValueError("Feishu credential must contain an official custom-bot webhook URL")
    token = parts[-1]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", token):
        raise ValueError("Feishu credential contains an invalid webhook token")
    return token


def _notify_type(severity: str) -> apprise.NotifyType:
    if severity in {"error", "critical"}:
        return apprise.NotifyType.FAILURE
    if severity == "warning":
        return apprise.NotifyType.WARNING
    return apprise.NotifyType.INFO


__all__ = [
    "AppriseSender",
    "NotificationSender",
    "RecordingSender",
    "render_text",
]
