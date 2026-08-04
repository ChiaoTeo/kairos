from __future__ import annotations

import asyncio
from uuid import uuid4

from kairospy.application.support.messaging.protocol import (
    Message,
    MessageInbox,
    SubscriptionClosed,
)


class _MemoryInbox(MessageInbox):
    def __init__(self, maxsize: int) -> None:
        self._queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=maxsize)
        self._closed = False
        self._closed_event = asyncio.Event()

    async def receive(self) -> Message:
        while True:
            if not self._queue.empty():
                item = self._queue.get_nowait()
                self._queue.task_done()
                return item
            if self._closed:
                raise SubscriptionClosed()
            queue_task = asyncio.create_task(self._queue.get())
            closed_task = asyncio.create_task(self._closed_event.wait())
            done, pending = await asyncio.wait(
                (queue_task, closed_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if queue_task in done:
                item = queue_task.result()
                self._queue.task_done()
                return item

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._closed_event.set()

    async def _deliver(self, message: Message) -> None:
        if not self._closed:
            await self._queue.put(message)


class InMemoryMessageBus:
    """A bounded, in-process bus with one System Inbox and global sequence."""

    def __init__(self) -> None:
        self._inbox: _MemoryInbox | None = None
        self._sequence = 0
        self._history: list[Message] = []
        self._lock = asyncio.Lock()
        self._closed = False

    def open_inbox(self, *, maxsize: int = 1024) -> MessageInbox:
        if self._closed:
            raise RuntimeError("message bus is closed")
        if maxsize < 0:
            raise ValueError("message inbox maxsize cannot be negative")
        if self._inbox is not None:
            raise RuntimeError("message bus already has an inbox")
        self._inbox = _MemoryInbox(maxsize)
        return self._inbox

    async def publish(self, message: Message) -> None:
        if self._closed:
            raise RuntimeError("message bus is closed")
        async with self._lock:
            self._sequence += 1
            global_sequence = self._sequence
            message_id = message.message_id or f"msg-{uuid4().hex}"
            accepted = Message(
                topic=message.topic,
                payload=message.payload,
                published_at=message.published_at,
                producer=message.producer,
                producer_sequence=message.producer_sequence,
                global_sequence=global_sequence,
                message_id=message_id,
                correlation_id=message.correlation_id,
                causation_id=message.causation_id,
                command_id=message.command_id,
            )
            self._history.append(accepted)
            if self._inbox is not None:
                await self._inbox._deliver(accepted)

    def history(self) -> tuple[Message, ...]:
        """Return accepted messages in Bus global-sequence order."""
        return tuple(self._history)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._inbox is not None:
            await self._inbox.close()


__all__ = ["InMemoryMessageBus"]
