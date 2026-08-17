"""Async live-event facade over the bounded Rust Aeron subscription."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Generic, TypeVar

from .generated_spec import DEFAULT_MAX_PAYLOAD_LEN
from .native import native


RecordT = TypeVar("RecordT")


class ResyncRequired(RuntimeError):
    """The live source lost continuity and must be recreated/resynchronized."""


class NativeEventSource(Generic[RecordT]):
    def __init__(
        self,
        *,
        decoder: Callable[[bytes], RecordT],
        aeron_dir: str | Path | None,
        channel: str,
        stream_id: int,
        max_payload_len: int = DEFAULT_MAX_PAYLOAD_LEN,
        queue_capacity: int = 1024,
    ) -> None:
        self._decoder = decoder
        self._aeron_dir = None if aeron_dir is None else str(aeron_dir)
        self._spec = native.StreamSpec(channel, stream_id, max_payload_len)
        self._queue_capacity = queue_capacity
        self._subscription = None
        self._closed = False

    @property
    def aeron_dir(self) -> str | None:
        return self._aeron_dir

    def _open(self):
        if self._closed:
            raise native.ClosedError("native event source is closed")
        if self._subscription is None:
            self._subscription = native.AeronSubscription(
                self._spec,
                aeron_dir=self._aeron_dir,
                queue_capacity=self._queue_capacity,
            )
        return self._subscription

    def check_ready(self) -> None:
        subscription = self._open()
        subscription.close()
        self._subscription = None

    async def subscribe_live(self) -> AsyncIterator[RecordT]:
        subscription = self._open()
        try:
            while True:
                frames = await asyncio.to_thread(subscription.poll, 64, 10)
                for payload in frames:
                    yield self._decoder(bytes(payload))
        except native.QueueOverflowError as error:
            await self.close()
            raise ResyncRequired("Aeron live queue overflowed") from error
        except asyncio.CancelledError:
            await self.close()
            raise

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._subscription is not None:
            subscription, self._subscription = self._subscription, None
            await asyncio.to_thread(subscription.close)

    async def __aenter__(self) -> NativeEventSource[RecordT]:
        self._open()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()


__all__ = ["NativeEventSource", "ResyncRequired"]
