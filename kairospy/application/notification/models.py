from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Literal, Mapping


NotificationSeverity = Literal["info", "warning", "error", "critical"]
NotificationPublishStatus = Literal["accepted", "duplicate", "rejected"]


@dataclass(frozen=True, slots=True)
class NotificationRequest:
    title: str
    body: str
    routes: tuple[str, ...] = ()
    severity: NotificationSeverity = "info"
    dedupe_key: str | None = None
    occurred_at: datetime | None = None
    attributes: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        title = self.title.strip()
        body = self.body.strip()
        routes = tuple(dict.fromkeys(route.strip() for route in self.routes))
        if not title:
            raise ValueError("notification title is required")
        if len(title) > 200:
            raise ValueError("notification title must not exceed 200 characters")
        if not body:
            raise ValueError("notification body is required")
        if len(body.encode("utf-8")) > 8 * 1024:
            raise ValueError("notification body must not exceed 8 KiB")
        if any(not route for route in routes):
            raise ValueError("notification routes must be non-empty strings")
        if self.severity not in {"info", "warning", "error", "critical"}:
            raise ValueError(f"unsupported notification severity: {self.severity}")
        dedupe_key = None if self.dedupe_key is None else self.dedupe_key.strip()
        if dedupe_key == "":
            raise ValueError("notification dedupe_key must not be empty")
        if dedupe_key is not None and len(dedupe_key) > 256:
            raise ValueError("notification dedupe_key must not exceed 256 characters")
        if self.occurred_at is not None and self.occurred_at.tzinfo is None:
            raise ValueError("notification occurred_at must be timezone-aware")
        attributes: dict[str, str] = {}
        if len(self.attributes) > 32:
            raise ValueError("notification attributes must not exceed 32 entries")
        for key, value in self.attributes.items():
            name = str(key).strip()
            if not name or len(name) > 64:
                raise ValueError("notification attribute keys must be 1-64 characters")
            if not isinstance(value, str) or len(value) > 512:
                raise ValueError(
                    "notification attribute values must be strings up to 512 characters"
                )
            attributes[name] = value
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "body", body)
        object.__setattr__(self, "routes", routes)
        object.__setattr__(self, "dedupe_key", dedupe_key)
        object.__setattr__(self, "attributes", MappingProxyType(attributes))


@dataclass(frozen=True, slots=True)
class NotificationReceipt:
    notification_id: str
    status: NotificationPublishStatus
    routes: tuple[str, ...]
    accepted_destinations: int
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class NotificationDestination:
    destination_id: str
    sender: Literal["feishu", "telegram", "recording"]
    credential_id: str | None = None
    settings: Mapping[str, str] = field(default_factory=dict)
    secrets: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.destination_id.strip():
            raise ValueError("notification destination_id is required")
        if self.sender not in {"feishu", "telegram", "recording"}:
            raise ValueError(f"unsupported notification sender: {self.sender}")
        object.__setattr__(self, "settings", MappingProxyType(dict(self.settings)))
        object.__setattr__(self, "secrets", MappingProxyType(dict(self.secrets)))


@dataclass(frozen=True, slots=True)
class RenderedNotification:
    notification_id: str
    title: str
    body: str
    severity: NotificationSeverity
    occurred_at: datetime
    attributes: Mapping[str, str]
    identity: Mapping[str, str]
    dedupe_key_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class SenderResult:
    outcome: Literal["delivered", "failed"]
    error_code: str | None = None

    @property
    def delivered(self) -> bool:
        return self.outcome == "delivered"


__all__ = [
    "NotificationDestination",
    "NotificationReceipt",
    "NotificationRequest",
    "NotificationSeverity",
    "RenderedNotification",
    "SenderResult",
]
