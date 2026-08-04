from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Message:
    """Immutable transport envelope with replay and causality metadata.

    ``producer_sequence`` belongs to the producer and is preserved by the
    bus.  ``global_sequence`` is assigned by the bus when the message is
    accepted.
    """

    topic: str
    payload: object
    published_at: datetime
    producer: str
    producer_sequence: int
    global_sequence: int = 0
    message_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    command_id: str | None = None

    def __post_init__(self) -> None:
        if not self.topic.strip():
            raise ValueError("message topic is required")
        if not self.producer.strip():
            raise ValueError("message producer is required")
        if self.producer_sequence < 1:
            raise ValueError("message producer sequence must be positive")
        if self.global_sequence < 0:
            raise ValueError("message global sequence cannot be negative")
        if self.published_at.tzinfo is None:
            raise ValueError("message published_at must be timezone-aware")

    @property
    def domain(self) -> str:
        return self.topic.split(".", 1)[0]

    @property
    def kind(self) -> str:
        return self.topic.split(".", 1)[1] if "." in self.topic else ""

    @property
    def time(self) -> datetime:
        return self.published_at

    @property
    def sequence(self) -> int:
        return self.global_sequence or self.producer_sequence

    def changed(self, domain: str, kind: str | None = None) -> bool:
        return self.domain == domain and (kind is None or self.kind == kind)


class SubscriptionClosed(Exception):
    """Raised when a subscription has been closed and drained."""


class MessageInbox(Protocol):
    async def receive(self) -> Message:
        ...

    async def close(self) -> None:
        ...


class MessageBus(Protocol):
    async def publish(self, message: Message) -> None:
        ...

    def open_inbox(self, *, maxsize: int = 1024) -> MessageInbox:
        """Open the single system inbox for this bus."""
        ...

    async def close(self) -> None:
        ...


__all__ = [
    "Message",
    "MessageBus",
    "MessageInbox",
    "SubscriptionClosed",
]
