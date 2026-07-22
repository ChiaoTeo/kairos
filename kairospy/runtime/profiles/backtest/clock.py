from __future__ import annotations

from datetime import datetime


class BacktestClock:
    def __init__(self) -> None:
        self._now: datetime | None = None

    @property
    def now(self) -> datetime:
        if self._now is None:
            raise RuntimeError("clock has not started")
        return self._now

    def advance(self, timestamp: datetime) -> None:
        if timestamp.tzinfo is None:
            raise ValueError("clock timestamps must be timezone-aware")
        if self._now is not None and timestamp < self._now:
            raise ValueError("backtest clock cannot move backwards")
        self._now = timestamp
