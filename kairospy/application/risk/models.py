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
    event_sequence: int
