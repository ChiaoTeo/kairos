"""Public application boundary for external notifications."""

from .application import (
    NotificationApplication,
    NotificationChannelConfig,
    NotificationResult,
    NotificationSettings,
    notification_issues,
    notification_settings,
)
from .domain import NotificationCategory, NotificationLevel, NotificationRequest
from .protocol import NotificationSender

__all__ = [
    "NotificationApplication",
    "NotificationCategory",
    "NotificationChannelConfig",
    "NotificationLevel",
    "NotificationRequest",
    "NotificationResult",
    "NotificationSettings",
    "NotificationSender",
    "notification_settings",
    "notification_issues",
]
