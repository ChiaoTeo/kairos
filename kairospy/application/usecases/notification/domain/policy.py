from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class NotificationLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class NotificationCategory(StrEnum):
    SYSTEM_LIFECYCLE = "system.lifecycle"
    SYSTEM_ERROR = "system.error"
    CONNECTION_HEALTH = "connection.health"
    EXECUTION_ORDER = "execution.order"
    EXECUTION_FILL = "execution.fill"
    RISK_ALERT = "risk.alert"
    ACCOUNT_SNAPSHOT = "account.snapshot"
    TRADING_SUMMARY = "trading.summary"


@dataclass(frozen=True, slots=True)
class NotificationRequest:
    """Business notification content independent of vendor payloads."""

    category: NotificationCategory
    title: str
    body: str
    level: NotificationLevel = NotificationLevel.INFO
    deduplication_key: str | None = None
    created_at: object | None = None

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("notification title is required")
        if not self.body.strip():
            raise ValueError("notification body is required")
        if self.deduplication_key is not None and not self.deduplication_key.strip():
            raise ValueError("notification deduplication key must not be blank")


__all__ = ["NotificationCategory", "NotificationLevel", "NotificationRequest"]
