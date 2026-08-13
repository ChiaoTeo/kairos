from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Callable


_DURATION = re.compile(
    r"^\s*(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>ns|us|ms|s|m|h|d)\s*$"
)


def parse_duration(value: str | int | float | timedelta) -> timedelta:
    if isinstance(value, timedelta):
        duration = value
    elif isinstance(value, (int, float)):
        duration = timedelta(seconds=float(value))
    elif isinstance(value, str):
        match = _DURATION.match(value)
        if match is None:
            raise ValueError(
                "duration must be a timedelta, seconds, or a value such as '1h'"
            )
        amount = float(match.group("value"))
        duration = {
            "ns": timedelta(microseconds=amount / 1_000),
            "us": timedelta(microseconds=amount),
            "ms": timedelta(milliseconds=amount),
            "s": timedelta(seconds=amount),
            "m": timedelta(minutes=amount),
            "h": timedelta(hours=amount),
            "d": timedelta(days=amount),
        }[match.group("unit")]
    else:
        raise TypeError("duration must be a timedelta, number, or string")
    if duration <= timedelta(0):
        raise ValueError("duration must be positive")
    return duration


@dataclass(frozen=True, slots=True)
class TimerEvent:
    timer_id: str
    scheduled_at: datetime
    event_time: datetime


@dataclass(slots=True)
class _TimerRegistration:
    timer_id: str
    next_due: datetime
    interval: timedelta | None
    catch_up: bool
    generation: int


class StrategyClock:
    """Deterministic strategy clock backed by the runtime event loop.

    The clock never sleeps and never reads wall-clock time.  The host advances
    it with a market or runtime event timestamp and then drains due timers.
    """

    def __init__(
        self,
        schedule: Callable[[str, datetime, timedelta | None, bool], None],
        cancel: Callable[[str], None],
    ) -> None:
        self._schedule = schedule
        self._cancel = cancel
        self._now: datetime | None = None

    @property
    def now(self) -> datetime | None:
        return self._now

    def _set_now(self, value: datetime | None) -> None:
        if value is not None and value.tzinfo is None:
            raise ValueError("clock time must be timezone-aware")
        self._now = value

    def every(
        self,
        timer_id: str,
        interval: str | int | float | timedelta,
        *,
        start_at: datetime | None = None,
        catch_up: bool = True,
    ) -> None:
        if not timer_id.strip():
            raise ValueError("timer_id is required")
        duration = parse_duration(interval)
        due = start_at or (None if self._now is None else self._now + duration)
        if due is None:
            raise ValueError("a timer needs a current clock time or start_at")
        if due.tzinfo is None:
            raise ValueError("timer start_at must be timezone-aware")
        self._schedule(timer_id, due, duration, catch_up)

    def at(self, timer_id: str, scheduled_at: datetime) -> None:
        if not timer_id.strip():
            raise ValueError("timer_id is required")
        if scheduled_at.tzinfo is None:
            raise ValueError("timer scheduled_at must be timezone-aware")
        self._schedule(timer_id, scheduled_at, None, False)

    def cancel(self, timer_id: str) -> None:
        if not timer_id.strip():
            raise ValueError("timer_id is required")
        self._cancel(timer_id)


class DeterministicTimerQueue:
    """Runtime-owned timer registry; business time is supplied by the caller."""

    def __init__(self) -> None:
        self._timers: dict[str, _TimerRegistration] = {}
        self._generation = 0

    def schedule(
        self,
        timer_id: str,
        due: datetime,
        interval: timedelta | None,
        catch_up: bool,
    ) -> None:
        self._generation += 1
        self._timers[timer_id] = _TimerRegistration(
            timer_id, due, interval, catch_up, self._generation
        )

    def cancel(self, timer_id: str) -> None:
        self._timers.pop(timer_id, None)

    def pop_due(self, now: datetime) -> list[TimerEvent]:
        if now.tzinfo is None:
            raise ValueError("clock time must be timezone-aware")
        due_events: list[TimerEvent] = []
        for timer_id in sorted(tuple(self._timers)):
            timer = self._timers.get(timer_id)
            if timer is None or timer.next_due > now:
                continue
            if timer.interval is None:
                due_events.append(TimerEvent(timer_id, timer.next_due, now))
                self._timers.pop(timer_id, None)
                continue
            if timer.catch_up:
                while timer.next_due <= now:
                    due_events.append(TimerEvent(timer_id, timer.next_due, now))
                    timer.next_due += timer.interval
            else:
                due_events.append(TimerEvent(timer_id, timer.next_due, now))
                timer.next_due = now + timer.interval
        return due_events

    def next_due(self) -> datetime | None:
        """Return the earliest scheduled business time, if any."""
        if not self._timers:
            return None
        return min(timer.next_due for timer in self._timers.values())


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("business time must be timezone-aware")
    return value.astimezone(timezone.utc)


__all__ = [
    "DeterministicTimerQueue",
    "StrategyClock",
    "TimerEvent",
    "ensure_utc",
    "parse_duration",
]
