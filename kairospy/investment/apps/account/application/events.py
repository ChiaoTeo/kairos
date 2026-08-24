from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from kairospy.investment.application.eventing import DataEvent
from kairospy.infrastructure.contracts.account.records import (
    AccountChangeRecord,
    AccountEventRecord,
    AccountFactProvenanceRecord,
)

from .models import (
    AccountStatusChange,
    Balance,
    EarnHolding,
    EquityChange,
    ObservedOrder,
    Position,
)


@dataclass(frozen=True, slots=True)
class BalanceChangedEvent(DataEvent[Balance]):
    kind: Literal["balance_changed"] = field(init=False, default="balance_changed")


@dataclass(frozen=True, slots=True)
class PositionChangedEvent(DataEvent[Position]):
    kind: Literal["position_changed"] = field(init=False, default="position_changed")


@dataclass(frozen=True, slots=True)
class EarnHoldingChangedEvent(DataEvent[EarnHolding]):
    kind: Literal["earn_holding_changed"] = field(
        init=False, default="earn_holding_changed"
    )


@dataclass(frozen=True, slots=True)
class EquityChangedEvent(DataEvent[EquityChange]):
    kind: Literal["equity_changed"] = field(init=False, default="equity_changed")


@dataclass(frozen=True, slots=True)
class AccountStatusChangedEvent(DataEvent[AccountStatusChange]):
    kind: Literal["status_changed"] = field(init=False, default="status_changed")


@dataclass(frozen=True, slots=True)
class ObservedOrderChangedEvent(DataEvent[ObservedOrder]):
    kind: Literal["observed_order_changed"] = field(
        init=False, default="observed_order_changed"
    )


AccountEvent: TypeAlias = (
    BalanceChangedEvent
    | PositionChangedEvent
    | EarnHoldingChangedEvent
    | EquityChangedEvent
    | AccountStatusChangedEvent
    | ObservedOrderChangedEvent
)
