from __future__ import annotations

from contextlib import contextmanager
import contextvars
from datetime import datetime
import json
import re
import sys
from typing import Any, Iterator, TextIO


_SECRET_KEYS = frozenset(
    {
        "api_key",
        "api_secret",
        "secret",
        "passphrase",
        "access_token",
        "private_key",
        "authorization",
        "proxy_authorization",
        "x_api_key",
        "x-api-key",
        "token",
    }
)
_PROMOTED_FIELDS = frozenset(
    {
        "event",
        "request_id",
        "event_id",
        "correlation_id",
        "causation_id",
        "duration_ms",
        "result",
        "error_code",
        "error_kind",
        "retryable",
        "operation",
        "cause",
    }
)
_SECRET_TEXT = re.compile(
    r"(?i)(bearer\s+|(?:api[_-]?key|api[_-]?secret|access[_-]?token|token|secret|password)\s*[=:]\s*)([^\s,;]+)"
)


class StrategyLogger:
    """Structured logger with system-time and event-time context."""

    def __init__(
        self, *, fields: dict[str, object] | None = None, stream: TextIO | None = None
    ) -> None:
        self._fields = dict(fields or {})
        self._stream = stream
        self._event_context: contextvars.ContextVar[dict[str, object]] = (
            contextvars.ContextVar("kairos_strategy_log_event_context", default={})
        )

    @contextmanager
    def bind_event(
        self,
        *,
        event_time: datetime | None,
        event_time_source: str,
        event_sequence: int,
    ) -> Iterator[None]:
        token = self._event_context.set(
            {
                "event_time": _timestamp(event_time),
                "event_time_source": event_time_source,
                "event_sequence": event_sequence,
            }
        )
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
            **_redact_mapping(self._fields),
            "schema_version": 1,
            "system_time": _timestamp(datetime.now().astimezone()),
            "event_time": None,
            "event_time_source": "none",
            "level": level,
            "event": "strategy_log",
            "message": _redact_text(message),
            **_trace_fields(),
        }
        record.update(self._event_context.get())
        redacted = _redact_mapping(data)
        for field in _PROMOTED_FIELDS:
            if field in redacted:
                record[field] = redacted.pop(field)
        if redacted:
            record["data"] = redacted
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


def _trace_fields() -> dict[str, str]:
    """Return valid current trace identifiers without making OTel mandatory."""

    try:
        from opentelemetry import trace

        context = trace.get_current_span().get_span_context()
    except ImportError:
        return {}
    if not context.is_valid:
        return {}
    return {
        "trace_id": f"{context.trace_id:032x}",
        "span_id": f"{context.span_id:016x}",
    }


def _redact_value(value: object, *, key: str | None = None) -> object:
    if key is not None and key.lower() in _SECRET_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return _redact_mapping(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_mapping(value: dict[str, object]) -> dict[str, object]:
    return {key: _redact_value(item, key=key) for key, item in value.items()}


def _redact_text(value: str) -> str:
    return _SECRET_TEXT.sub(lambda match: f"{match.group(1)}[REDACTED]", value)


__all__ = ["StrategyLogger", "StrategyOutput"]
