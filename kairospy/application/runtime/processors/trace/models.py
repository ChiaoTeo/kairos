from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class DecisionTraceRecord:
    time: datetime | None
    strategy_id: str
    name: str
    payload: Mapping[str, object] = field(default_factory=dict)
    intent_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
        object.__setattr__(self, "intent_ids", tuple(str(item) for item in self.intent_ids))


@dataclass(frozen=True, slots=True)
class DecisionTraceView:
    records: tuple[DecisionTraceRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class RiskPositionSnapshot:
    instrument_id: str
    quantity: Decimal
    mark_price: Decimal | None = None
    notional: Decimal | None = None


@dataclass(frozen=True, slots=True)
class FundingRateSnapshot:
    market_id: str | None
    instrument_id: str | None
    rate: Decimal
    mark_price: Decimal | None
    time: datetime
    basis: str = "funding_rate"


@dataclass(frozen=True, slots=True)
class RiskSnapshot:
    time: datetime
    account_id: str
    cash: Decimal
    equity: Decimal
    gross_notional: Decimal
    net_notional: Decimal
    positions: tuple[RiskPositionSnapshot, ...] = ()
    funding_rates: tuple[FundingRateSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class RiskSnapshotsView:
    snapshots: tuple[RiskSnapshot, ...] = ()


__all__ = [
    "DecisionTraceRecord",
    "DecisionTraceView",
    "FundingRateSnapshot",
    "RiskPositionSnapshot",
    "RiskSnapshot",
    "RiskSnapshotsView",
]
