from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from kairospy.domain_types import AccountId


@dataclass(frozen=True, slots=True)
class RiskViolation:
    code: str
    message: str
    limit: Decimal | None = None
    actual: Decimal | None = None


@dataclass(frozen=True, slots=True)
class RiskStatus:
    account_id: AccountId
    trading_allowed: bool
    available_notional: Decimal | None
    reserved_notional: Decimal
    utilization: Decimal | None
    violations: tuple[RiskViolation, ...]
    generation: int


@dataclass(frozen=True, slots=True)
class ReservationChange:
    reservation_id: str
    request_id: str
    account_id: AccountId
    strategy_id: str
    status: str
    occurred_at_unix_nanos: int


@dataclass(frozen=True, slots=True)
class RiskDecisionChange:
    decision_id: str
    request_id: str
    account_id: AccountId
    strategy_id: str
    allowed: bool
    degraded: bool
    reason_codes: tuple[str, ...]
    violations: tuple[str, ...]
    occurred_at_unix_nanos: int


@dataclass(frozen=True, slots=True)
class RiskCircuitChange:
    account_id: AccountId | None
    strategy_id: str | None
    exchange_id: str | None
    open: bool
    reason: str
    opened_at_unix_nanos: int | None
    reset_at_unix_nanos: int | None
    occurred_at_unix_nanos: int
