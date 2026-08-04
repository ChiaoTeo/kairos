from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping

from kairospy.domain.views import ViewFieldSchema, ViewSchema


class TraceViewKeys:
    decision_trace = "strategy.decision_trace"
    risk_snapshots = "account.risk_snapshots"


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


DECISION_TRACE_SCHEMA = ViewSchema(
    TraceViewKeys.decision_trace,
    "system",
    fields=(ViewFieldSchema("records", "strategy decision trace records", "runtime state", "strategy trace hook"),),
    mutability="runtime_writable",
    evidence="strategy emitted decision trace records",
)

RISK_SNAPSHOTS_SCHEMA = ViewSchema(
    TraceViewKeys.risk_snapshots,
    "system",
    fields=(ViewFieldSchema("snapshots", "account risk snapshots over time", "runtime state", "account ledger and market marks"),),
    mutability="runtime_writable",
    evidence="marked account risk snapshots",
)


__all__ = [
    "DECISION_TRACE_SCHEMA",
    "RISK_SNAPSHOTS_SCHEMA",
    "DecisionTraceRecord",
    "DecisionTraceView",
    "FundingRateSnapshot",
    "RiskPositionSnapshot",
    "RiskSnapshot",
    "RiskSnapshotsView",
    "TraceViewKeys",
]
