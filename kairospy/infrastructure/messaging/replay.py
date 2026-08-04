"""Deterministic replay helper for a recorded Message history."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable

from kairospy.application.support.messaging import Message


async def replay_messages(
    messages: Iterable[Message],
    consumer: Callable[[Message], Awaitable[object]],
    *,
    after_sequence: int = 0,
) -> None:
    """Drive one System consumer in recorded global-sequence order."""
    if after_sequence < 0:
        raise ValueError("replay after_sequence cannot be negative")
    ordered = sorted(
        (message for message in messages if message.global_sequence > after_sequence),
        key=lambda message: (message.global_sequence, message.producer, message.producer_sequence),
    )
    for message in ordered:
        await consumer(message)


__all__ = ["replay_messages"]
