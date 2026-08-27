"""Reference event source using owner-native decoding."""

from __future__ import annotations

from pathlib import Path

from kairospy.infrastructure.transport.native_event import NativeEventSource

from .events import ReferenceEvent, _native, decode_event

DEFAULT_CHANNEL = str(_native().DEFAULT_AERON_CHANNEL)
REFERENCE_CHANGES = int(_native().REFERENCE_EVENT_STREAM_ID)


class AeronReferenceEventSource(NativeEventSource[ReferenceEvent]):
    def __init__(
        self,
        *,
        aeron_dir: str | Path | None = None,
        channel: str = DEFAULT_CHANNEL,
        stream_id: int = REFERENCE_CHANGES,
    ) -> None:
        super().__init__(
            decoder=decode_event,
            aeron_dir=aeron_dir,
            channel=channel,
            stream_id=stream_id,
        )


decode_reference_event = decode_event

__all__ = ["AeronReferenceEventSource", "decode_reference_event"]
