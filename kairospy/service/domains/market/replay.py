from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from time import monotonic, sleep
from typing import Callable, Mapping


RowWriter = Callable[[Iterable[Mapping[str, object]]], None]


def replay_rows(rows: Iterable[Mapping[str, object]], *, speed: float, write: RowWriter) -> int:
    if speed < 0:
        raise ValueError("replay speed cannot be negative")
    previous_time: float | None = None
    wall_start = monotonic()
    replay_start: float | None = None
    for row in rows:
        current_time = _timestamp(row["time"])
        if speed > 0:
            if replay_start is None:
                replay_start = current_time
            target_elapsed = (current_time - replay_start) / speed
            sleep_seconds = target_elapsed - (monotonic() - wall_start)
            if previous_time is not None and sleep_seconds > 0:
                sleep(sleep_seconds)
        previous_time = current_time
        write((row,))
    return 0


def _timestamp(value: object) -> float:
    if not isinstance(value, str):
        raise ValueError(f"replay row time must be ISO-8601 text: {value!r}")
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"replay row time must be timezone-aware: {value!r}")
    return parsed.astimezone(timezone.utc).timestamp()


__all__ = ["RowWriter", "replay_rows"]
