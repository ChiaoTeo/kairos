from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from enum import StrEnum

from kairospy.domain.product import OptionRight


class MarketDataType(StrEnum):
    REALTIME = "realtime"
    FROZEN = "frozen"
    DELAYED = "delayed"
    DELAYED_FROZEN = "delayed_frozen"

    @property
    def ibkr_code(self) -> int:
        return {
            self.REALTIME: 1,
            self.FROZEN: 2,
            self.DELAYED: 3,
            self.DELAYED_FROZEN: 4,
        }[self]


@dataclass(frozen=True, slots=True)
class OptionChainCaptureSpec:
    underlying: str = "SPX"
    trading_class: str = "SPXW"
    exchange: str = "SMART"
    underlying_exchange: str = "CBOE"
    currency: str = "USD"
    expiry_count: int = 1
    strikes_each_side: int = 10
    rights: tuple[OptionRight, ...] = (OptionRight.CALL, OptionRight.PUT)
    market_data_type: MarketDataType = MarketDataType.DELAYED
    quote_timeout_seconds: float = 10.0
    max_quote_age_seconds: float = 5.0
    minimum_dte_days: int = 0
    maximum_dte_days: int | None = None
    target_dte_days: int | None = None
    minimum_strike_moneyness: float | None = None
    maximum_strike_moneyness: float | None = None
    maximum_strikes: int | None = None
    retain_delta_legs: bool = False
    retention_evaluation_time: str = "15:30:00"
    retention_target_deltas: tuple[float, ...] = (-0.25, -0.10)
    retention_until_dte: int = 3

    def __post_init__(self) -> None:
        if self.expiry_count < 1:
            raise ValueError("expiry_count must be at least 1")
        if self.strikes_each_side < 0:
            raise ValueError("strikes_each_side cannot be negative")
        if self.quote_timeout_seconds <= 0 or self.max_quote_age_seconds <= 0:
            raise ValueError("timeouts must be positive")
        if not self.rights:
            raise ValueError("at least one option right is required")
        if self.minimum_dte_days < 0 or self.maximum_dte_days is not None and self.maximum_dte_days < self.minimum_dte_days:
            raise ValueError("invalid option-chain DTE range")
        if self.target_dte_days is not None and self.target_dte_days < self.minimum_dte_days:
            raise ValueError("target DTE cannot be below minimum DTE")
        if self.maximum_dte_days is not None and self.target_dte_days is not None and self.target_dte_days > self.maximum_dte_days:
            raise ValueError("target DTE cannot exceed maximum DTE")
        if (self.minimum_strike_moneyness is None) != (self.maximum_strike_moneyness is None):
            raise ValueError("both strike moneyness bounds must be provided")
        if self.minimum_strike_moneyness is not None and not 0 < self.minimum_strike_moneyness < self.maximum_strike_moneyness:
            raise ValueError("invalid strike moneyness range")
        if self.maximum_strikes is not None and self.maximum_strikes < 3:
            raise ValueError("maximum strikes must be at least three")
        if self.retention_until_dte < 0 or not self.retention_target_deltas:
            raise ValueError("invalid retained-leg configuration")
        time.fromisoformat(self.retention_evaluation_time)
