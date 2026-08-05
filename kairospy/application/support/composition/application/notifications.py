from __future__ import annotations

from collections.abc import Mapping

from kairospy.application.usecases.notification import NotificationApplication, notification_settings
from kairospy.infrastructure.notifications import (
    FeishuNotificationSender,
    HttpNotificationTransport,
    TelegramNotificationSender,
    WeComNotificationSender,
)


def build_notification_application(config: object) -> NotificationApplication | None:
    values = config if isinstance(config, Mapping) else {}
    settings = notification_settings(values.get("notifications"))
    if not settings.enabled:
        return None
    senders = []
    transport = HttpNotificationTransport()
    for channel in settings.channels:
        if not channel.enabled:
            continue
        values = channel.values
        if channel.name == "feishu":
            senders.append(FeishuNotificationSender(str(values["webhook_url"]), secret=_optional(values.get("secret")), transport=transport))
        elif channel.name == "wecom":
            senders.append(WeComNotificationSender(str(values["webhook_url"]), transport=transport))
        elif channel.name == "telegram":
            senders.append(
                TelegramNotificationSender(
                    str(values["bot_token"]),
                    str(values["chat_id"]),
                    parse_mode=_optional(values.get("parse_mode")),
                    transport=transport,
                )
            )
    if not senders:
        return None
    rules = {
        channel.name: tuple(str(value) for value in channel.values.get("categories", ()))
        for channel in settings.channels
        if isinstance(channel.values.get("categories", ()), (list, tuple))
    }
    return NotificationApplication(
        senders,
        enabled_categories=rules,
        max_attempts=settings.max_attempts,
        minimum_level=settings.minimum_level,
        rate_limit_per_minute=settings.rate_limit_per_minute,
        circuit_breaker_failures=settings.circuit_breaker_failures,
        circuit_recovery_seconds=settings.circuit_recovery_seconds,
    )


def notification_runtime_settings(config: object) -> object:
    values = config if isinstance(config, Mapping) else {}
    return notification_settings(values.get("notifications"))


def _optional(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


__all__ = ["build_notification_application", "notification_runtime_settings"]
