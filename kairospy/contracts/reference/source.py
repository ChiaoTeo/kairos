"""Reference event source using owner-native decoding."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from types import ModuleType

from kairospy.contracts.reference.events import ReferenceEventVariant
from .events import decode_event


def _native() -> ModuleType:
    return import_module("kairospy._native_reference_contract")

DEFAULT_CHANNEL = str(_native().DEFAULT_AERON_CHANNEL)
REFERENCE_CHANGES = int(_native().REFERENCE_EVENT_STREAM_ID)


class AeronReferenceEventSource:
    def __init__(
        self,
        *,
        aeron_dir: str | Path | None = None,
        channel: str = DEFAULT_CHANNEL,
        stream_id: int = REFERENCE_CHANGES,
    ) -> None:
        self._subscription = _native().ReferenceLiveSubscription(
            aeron_dir=aeron_dir,
            channel=channel,
            stream_id=stream_id,
        )

    def poll_visit(
        self,
        visitor: Callable[[ReferenceEventVariant], None],
        *,
        fragment_limit: int = 64,
    ) -> int:
        return int(
            self._subscription.poll_visit(visitor, fragment_limit=fragment_limit)
        )

    def close(self) -> None:
        self._subscription.close()


decode_reference_event = decode_event

__all__ = ["AeronReferenceEventSource", "decode_reference_event"]
