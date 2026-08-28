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
class ClientOrderId(TextValue):
    """Canonical client-assigned order identity."""


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


@dataclass(frozen=True, slots=True)
class OrderOptionCode(TextValue):
    """Canonical Execution order-option code."""


@dataclass(frozen=True, slots=True)
class OrderEntrySymbol(TextValue):
    """Canonical symbol used at an Execution order-entry boundary."""


@dataclass(frozen=True, slots=True)
class ExecutionChannelCode(TextValue):
    """Canonical Execution channel code."""


IntentIdRead = NewType("IntentIdRead", str)
OrderIdRead = NewType("OrderIdRead", str)
ClientOrderIdRead = NewType("ClientOrderIdRead", str)
FillIdRead = NewType("FillIdRead", str)
ExecutionRouteIdRead = NewType("ExecutionRouteIdRead", str)
PlanIdRead = NewType("PlanIdRead", str)
LegIdRead = NewType("LegIdRead", str)
OrderOptionCodeRead = NewType("OrderOptionCodeRead", str)
OrderEntrySymbolRead = NewType("OrderEntrySymbolRead", str)
ExecutionChannelCodeRead = NewType("ExecutionChannelCodeRead", str)


__all__ = [
    "FillId",
    "FillIdRead",
    "ClientOrderId",
    "ClientOrderIdRead",
    "ExecutionChannelCode",
    "ExecutionChannelCodeRead",
    "ExecutionRouteId",
    "ExecutionRouteIdRead",
    "IntentId",
    "IntentIdRead",
    "LegId",
    "LegIdRead",
    "OrderId",
    "OrderIdRead",
    "OrderEntrySymbol",
    "OrderEntrySymbolRead",
    "OrderOptionCode",
    "OrderOptionCodeRead",
    "PlanId",
    "PlanIdRead",
]
