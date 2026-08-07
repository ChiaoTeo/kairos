from __future__ import annotations

from contextlib import contextmanager
import contextvars
from datetime import datetime
import json
import sys
from typing import Any, Iterator, TextIO


class StrategyLogger:
    """Structured logger with system-time and event-time context."""

    def __init__(self, *, fields: dict[str, object] | None = None, stream: TextIO | None = None) -> None:
        self._fields = dict(fields or {})
        self._stream = stream
        self._event_context: contextvars.ContextVar[dict[str, object]] = contextvars.ContextVar(
            "kairos_strategy_log_event_context", default={}
        )

    @contextmanager
    def bind_event(
        self,
        *,
        event_time: datetime | None,
        event_time_source: str,
        event_sequence: int,
    ) -> Iterator[None]:
        token = self._event_context.set({
            "event_time": _timestamp(event_time),
            "event_time_source": event_time_source,
            "event_sequence": event_sequence,
        })
        try:
            yield
        finally:
            self._event_context.reset(token)

    def info(self, message: str, **data: object) -> None:
        self.log("info", message, **data)

    def warning(self, message: str, **data: object) -> None:
        self.log("warning", message, **data)

    def error(self, message: str, **data: object) -> None:
        self.log("error", message, **data)

    def log(self, level: str, message: str, **data: object) -> None:
        record: dict[str, Any] = {
            **self._fields,
            "system_time": _timestamp(datetime.now().astimezone()),
            "event_time": None,
            "event_time_source": "none",
            "level": level,
            "message": message,
        }
        record.update(self._event_context.get())
        if data:
            record["data"] = data
        stream = self._stream or sys.stdout
        stream.write(json.dumps(record, default=str, separators=(",", ":")) + "\n")
        stream.flush()


class StrategyOutput:
    """File-like adapter that turns legacy strategy print output into logs."""

    def __init__(self, logger: StrategyLogger, *, source: str) -> None:
        self.logger = logger
        self.source = source
        self._buffer = ""

    def write(self, value: str) -> int:
        self._buffer += value
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line:
                self.logger.info(line, source=self.source)
        return len(value)

    def flush(self) -> None:
        if self._buffer:
            self.logger.info(self._buffer, source=self.source)
            self._buffer = ""


def _timestamp(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


__all__ = ["StrategyLogger", "StrategyOutput"]
