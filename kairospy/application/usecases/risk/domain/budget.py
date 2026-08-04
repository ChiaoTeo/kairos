from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class RiskMetric(StrEnum):
    NOTIONAL = "notional"
    MARGIN = "margin"
    GROSS_EXPOSURE = "gross_exposure"
    NET_EXPOSURE = "net_exposure"
    TURNOVER = "turnover"
    ORDER_RATE = "order_rate"


class RiskDecision(StrEnum):
    ALLOWED = "allowed"
    REDUCED = "reduced"
    REJECTED = "rejected"


class RiskReservationStatus(StrEnum):
    RESERVED = "reserved"
    PARTIALLY_CONSUMED = "partially_consumed"
    CONSUMED = "consumed"
    RELEASED = "released"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class BudgetRef:
    scope: str
    subject: str

    def __post_init__(self) -> None:
        if not self.scope.strip() or not self.subject.strip():
            raise ValueError("budget scope and subject cannot be empty")


@dataclass(frozen=True, slots=True)
class RiskUsage:
    metric: RiskMetric
    amount: Decimal
    budgets: tuple[BudgetRef, ...]

    def __post_init__(self) -> None:
        if self.amount < 0:
            raise ValueError("risk usage cannot be negative")
        if not self.budgets:
            raise ValueError("risk usage requires at least one budget")


@dataclass(frozen=True, slots=True)
class RiskBudget:
    budget_id: str
    ref: BudgetRef
    metric: RiskMetric
    limit: Decimal
    used: Decimal = Decimal("0")
    reserved: Decimal = Decimal("0")
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    def __post_init__(self) -> None:
        if not self.budget_id.strip():
            raise ValueError("budget_id cannot be empty")
        if self.limit < 0 or self.used < 0 or self.reserved < 0:
            raise ValueError("risk budget amounts cannot be negative")
        if self.used + self.reserved > self.limit:
            raise ValueError("risk budget usage exceeds limit")
        if self.valid_from and self.valid_from.tzinfo is None:
            raise ValueError("valid_from must be timezone-aware")
        if self.valid_until and self.valid_until.tzinfo is None:
            raise ValueError("valid_until must be timezone-aware")
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("valid_until cannot precede valid_from")

    @property
    def available(self) -> Decimal:
        return self.limit - self.used - self.reserved

    def active_at(self, at: datetime) -> bool:
        if at.tzinfo is None:
            raise ValueError("risk evaluation timestamp must be timezone-aware")
        return (self.valid_from is None or at >= self.valid_from) and (
            self.valid_until is None or at < self.valid_until
        )


@dataclass(frozen=True, slots=True)
class RiskAllocation:
    budget_id: str
    metric: RiskMetric
    amount: Decimal


@dataclass(frozen=True, slots=True)
class RiskReservation:
    reservation_id: str
    request_id: str
    allocations: tuple[RiskAllocation, ...]
    status: RiskReservationStatus
    created_at: datetime


__all__ = [
    "BudgetRef",
    "RiskAllocation",
    "RiskBudget",
    "RiskDecision",
    "RiskMetric",
    "RiskReservation",
    "RiskReservationStatus",
    "RiskUsage",
]
