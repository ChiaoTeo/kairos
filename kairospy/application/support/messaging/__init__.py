"""Transport-neutral messaging contracts used by runtime composition."""

from .protocol import (
    Message,
    MessageBus,
    MessageInbox,
    SubscriptionClosed,
)

__all__ = [
    "Message",
    "MessageBus",
    "MessageInbox",
    "SubscriptionClosed",
]
