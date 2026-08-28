from __future__ import annotations

from dataclasses import dataclass
from kairospy.primitives.account import AccountId
from kairospy.primitives.decimal import DecimalValue, Money, MoneyLike, Rate, RateLike
from kairospy.primitives.time import Generation


@dataclass(frozen=True, slots=True)
class RiskViolation:
    code: str
    message: str
    limit: DecimalValue | None = None
    actual: DecimalValue | None = None


@dataclass(frozen=True, slots=True)
class RiskStatus:
    account_id: AccountId
    trading_allowed: bool
    available_notional: MoneyLike | None
    reserved_notional: MoneyLike
    utilization: RateLike | None
    violations: tuple[RiskViolation, ...]
    generation: Generation

    def __post_init__(self) -> None:
        if self.available_notional is not None and not isinstance(
            self.available_notional, MoneyLike
        ):
            object.__setattr__(
                self, "available_notional", Money(self.available_notional)
            )
        if not isinstance(self.reserved_notional, MoneyLike):
            object.__setattr__(self, "reserved_notional", Money(self.reserved_notional))
        if self.utilization is not None and not isinstance(self.utilization, RateLike):
            object.__setattr__(self, "utilization", Rate(self.utilization))
        object.__setattr__(self, "generation", Generation(self.generation))
