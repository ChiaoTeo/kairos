"""Ports consumed by the risk usecase."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .domain import RiskUsage


class RiskMeasurementSource(Protocol):
    """Optional source for measured portfolio usage.

    The risk domain deliberately consumes a small risk-specific projection instead
    of importing account, order, or execution internals.
    """

    def usages(self, *, at: datetime) -> tuple[RiskUsage, ...]:
        ...


__all__ = ["RiskMeasurementSource"]
