from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping

from ..domain import NotificationLevel


@dataclass(frozen=True, slots=True)
class NotificationChannelConfig:
    name: str
    enabled: bool
    values: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class NotificationSettings:
    enabled: bool = False
    summary_interval_seconds: float | None = None
    queue_size: int = 256
    max_attempts: int = 3
    minimum_level: NotificationLevel = NotificationLevel.INFO
    rate_limit_per_minute: int | None = None
    circuit_breaker_failures: int = 5
    circuit_recovery_seconds: float = 30.0
    channels: tuple[NotificationChannelConfig, ...] = ()


def notification_settings(raw: object) -> NotificationSettings:
    if raw is None:
        return NotificationSettings()
    if not isinstance(raw, Mapping):
        raise ValueError("notifications must be a table")
    enabled = _bool(raw.get("enabled", False), "notifications.enabled")
    interval = _duration(raw.get("summary_interval"), "notifications.summary_interval")
    queue_size = _positive_int(raw.get("queue_size", 256), "notifications.queue_size")
    max_attempts = _positive_int(raw.get("max_attempts", 3), "notifications.max_attempts")
    minimum_level = _level(raw.get("minimum_level", NotificationLevel.INFO.value))
    rate_limit = _optional_positive_int(raw.get("rate_limit_per_minute"), "notifications.rate_limit_per_minute")
    circuit_failures = _positive_int(raw.get("circuit_breaker_failures", 5), "notifications.circuit_breaker_failures")
    recovery_seconds = _positive_float(raw.get("circuit_recovery_seconds", 30), "notifications.circuit_recovery_seconds")
    channels: list[NotificationChannelConfig] = []
    for name in ("feishu", "wecom", "telegram"):
        value = raw.get(name)
        if value is None:
            continue
        if not isinstance(value, Mapping):
            raise ValueError(f"notifications.{name} must be a table")
        channel_enabled = _bool(value.get("enabled", True), f"notifications.{name}.enabled")
        values = {str(key): _resolve_secret(value[key]) for key in value}
        categories = values.get("categories")
        if categories is not None and not isinstance(categories, (list, tuple)):
            raise ValueError(f"notifications.{name}.categories must be an array")
        if isinstance(categories, (list, tuple)) and any(not str(item).strip() for item in categories):
            raise ValueError(f"notifications.{name}.categories must contain non-empty strings")
        if enabled and channel_enabled:
            _validate_channel(name, values)
        channels.append(NotificationChannelConfig(name, channel_enabled, values))
    return NotificationSettings(enabled, interval, queue_size, max_attempts, minimum_level, rate_limit, circuit_failures, recovery_seconds, tuple(channels))


def notification_issues(raw: object) -> tuple[str, ...]:
    try:
        notification_settings(raw)
    except ValueError as error:
        return (str(error),)
    return ()


def _validate_channel(name: str, values: Mapping[str, object]) -> None:
    required = {
        "feishu": ("webhook_url",),
        "wecom": ("webhook_url",),
        "telegram": ("bot_token", "chat_id"),
    }[name]
    missing = [key for key in required if not str(values.get(key, "")).strip()]
    if missing:
        raise ValueError(f"notifications.{name} requires: {', '.join(missing)}")


def _level(value: object) -> NotificationLevel:
    try:
        return NotificationLevel(str(value).strip().lower())
    except ValueError as error:
        raise ValueError("notifications.minimum_level must be info, warning, or error") from error


def _resolve_secret(value: object) -> object:
    if not isinstance(value, str):
        return value
    match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", value.strip())
    if match is None:
        return value
    return os.environ.get(match.group(1), "")


def _bool(value: object, source: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{source} must be a boolean")
    return value


def _positive_int(value: object, source: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{source} must be a positive integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{source} must be a positive integer") from error
    if number < 1:
        raise ValueError(f"{source} must be a positive integer")
    return number


def _optional_positive_int(value: object, source: str) -> int | None:
    return None if value is None else _positive_int(value, source)


def _positive_float(value: object, source: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{source} must be positive") from error
    if number <= 0:
        raise ValueError(f"{source} must be positive")
    return number


def _duration(value: object, source: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value <= 0:
            raise ValueError(f"{source} must be positive")
        return float(value)
    text = str(value).strip().lower()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(s|m|h|d)", text)
    if match is None:
        raise ValueError(f"{source} must be a duration such as 5m or 1h")
    amount = float(match.group(1))
    return amount * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]


__all__ = ["NotificationChannelConfig", "NotificationSettings", "notification_issues", "notification_settings"]
