from __future__ import annotations

from dataclasses import dataclass
from typing import NewType

from ._text import TextValue


@dataclass(frozen=True, slots=True)
class IntentId(TextValue):
    """Canonical Execution intent identity."""


@dataclass(frozen=True, slots=True)
class OrderId(TextValue):
    """Canonical exchange-facing order identity."""


@dataclass(frozen=True, slots=True)
class FillId(TextValue):
    """Canonical Execution fill identity."""


@dataclass(frozen=True, slots=True)
class ExecutionRouteId(TextValue):
    """Canonical Execution route identity."""


@dataclass(frozen=True, slots=True)
class PlanId(TextValue):
    """Canonical Execution plan identity."""


@dataclass(frozen=True, slots=True)
class LegId(TextValue):
    """Canonical Execution plan leg identity."""


IntentIdRead = NewType("IntentIdRead", str)
OrderIdRead = NewType("OrderIdRead", str)
FillIdRead = NewType("FillIdRead", str)
ExecutionRouteIdRead = NewType("ExecutionRouteIdRead", str)
PlanIdRead = NewType("PlanIdRead", str)
LegIdRead = NewType("LegIdRead", str)


__all__ = [
    "FillId",
    "FillIdRead",
    "ExecutionRouteId",
    "ExecutionRouteIdRead",
    "IntentId",
    "IntentIdRead",
    "LegId",
    "LegIdRead",
    "OrderId",
    "OrderIdRead",
    "PlanId",
    "PlanIdRead",
]
