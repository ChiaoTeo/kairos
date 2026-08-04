"""In-process message delivery policy."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from kairospy.application.support.messaging import Message


MessageDelivery = Callable[[Message], object | Awaitable[object]]


class MessageDeliveryPolicy:
    """Provide one place for deduplication and bounded message retries."""

    def __init__(self, *, max_attempts: int = 1) -> None:
        if max_attempts < 1:
            raise ValueError("message max_attempts must be positive")
        self.max_attempts = max_attempts
        self._processed: set[str] = set()

    async def deliver(self, message: Message, handler: MessageDelivery) -> tuple[bool, object | None]:
        """Deliver once; return ``False`` when the message was already done."""
        message_id = message.message_id
        if message_id is not None and message_id in self._processed:
            return False, None
        for attempt in range(self.max_attempts):
            try:
                result = handler(message)
                if inspect.isawaitable(result):
                    result = await result
            except Exception:
                if attempt + 1 == self.max_attempts:
                    raise
            else:
                if message_id is not None:
                    self._processed.add(message_id)
                return True, result
        return False, None

    @property
    def processed_message_ids(self) -> frozenset[str]:
        return frozenset(self._processed)


__all__ = ["MessageDeliveryPolicy"]
