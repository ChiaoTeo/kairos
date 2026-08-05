from __future__ import annotations

from collections import deque
from time import monotonic


class ChannelResilience:
    """Process-local rate limit and circuit breaker for one channel."""

    def __init__(self, *, rate_limit_per_minute: int | None = None, failure_threshold: int = 5, recovery_seconds: float = 30.0) -> None:
        self.rate_limit_per_minute = rate_limit_per_minute
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._sent_at: deque[float] = deque()
        self._failures = 0
        self._open_until = 0.0

    def allow(self) -> bool:
        now = monotonic()
        if now < self._open_until:
            return False
        if self.rate_limit_per_minute is None:
            return True
        cutoff = now - 60.0
        while self._sent_at and self._sent_at[0] <= cutoff:
            self._sent_at.popleft()
        return len(self._sent_at) < self.rate_limit_per_minute

    def success(self) -> None:
        self._sent_at.append(monotonic())
        self._failures = 0
        self._open_until = 0.0

    def failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._open_until = monotonic() + self.recovery_seconds

    @property
    def state(self) -> str:
        if monotonic() < self._open_until:
            return "open"
        if self._failures:
            return "degraded"
        return "ready"


__all__ = ["ChannelResilience"]
