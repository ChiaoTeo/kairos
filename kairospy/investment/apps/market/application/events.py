from __future__ import annotations

class EventStreamGap(RuntimeError):
    """A Market stream skipped a sequence and requires event-native resync."""

    def __init__(self, stream_id: str, expected: int, actual: int) -> None:
        super().__init__(
            f"event stream {stream_id} gap: expected sequence {expected}, received {actual}"
        )
        self.stream_id = stream_id
        self.expected = expected
        self.actual = actual
__all__ = ["EventStreamGap"]
