from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from kairospy.application.clock import Clock, SystemClock


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class OperationalAlert:
    code: str
    severity: AlertSeverity
    message: str
    timestamp: datetime
    venue: str | None = None


class OperationalMonitor:
    def __init__(self, maximum_clock_skew_ms: int = 1000,
                 rate_limit_warning_fraction: Decimal = Decimal("0.80"), clock: Clock | None = None) -> None:
        self.maximum_clock_skew_ms = maximum_clock_skew_ms
        self.rate_limit_warning_fraction = rate_limit_warning_fraction
        self.clock = clock or SystemClock()
        self._alerts: list[OperationalAlert] = []

    @property
    def alerts(self) -> tuple[OperationalAlert, ...]:
        return tuple(self._alerts)

    def clock_skew(self, venue: str, skew_ms: int) -> None:
        if abs(skew_ms) > self.maximum_clock_skew_ms:
            self._add("clock_skew", AlertSeverity.CRITICAL, f"clock skew {skew_ms}ms exceeds limit", venue)

    def rate_limit(self, venue: str, used: int, limit: int) -> None:
        if limit <= 0:
            raise ValueError("rate limit must be positive")
        if Decimal(used) / Decimal(limit) >= self.rate_limit_warning_fraction:
            self._add("rate_limit", AlertSeverity.WARNING, f"rate limit usage {used}/{limit}", venue)

    def disconnected(self, venue: str, reason: str) -> None:
        self._add("disconnect", AlertSeverity.CRITICAL, reason, venue)

    def authentication_error(self, venue: str, reason: str) -> None:
        self._add("authentication", AlertSeverity.CRITICAL, reason, venue)

    def _add(self, code: str, severity: AlertSeverity, message: str, venue: str | None) -> None:
        self._alerts.append(OperationalAlert(code, severity, message, self.clock.now(), venue))
