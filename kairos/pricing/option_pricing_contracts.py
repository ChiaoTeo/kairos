from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from kairos.domain.product import OptionRight


class PricingModel(StrEnum):
    BLACK_SCHOLES = "black_scholes"
    BLACK_76 = "black_76"


class SolverStatus(StrEnum):
    CONVERGED = "converged"
    PRICE_OUT_OF_BOUNDS = "price_out_of_bounds"
    NOT_BRACKETED = "not_bracketed"
    MAX_ITERATIONS = "max_iterations"


@dataclass(frozen=True, slots=True)
class PricingInput:
    underlying: Decimal
    strike: Decimal
    time_to_expiry: Decimal
    risk_free_rate: Decimal
    volatility: Decimal
    right: OptionRight
    dividend_yield: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.underlying <= 0 or self.strike <= 0:
            raise ValueError("underlying and strike must be positive")
        if self.time_to_expiry < 0:
            raise ValueError("time to expiry cannot be negative")
        if self.volatility < 0:
            raise ValueError("volatility cannot be negative")


@dataclass(frozen=True, slots=True)
class PricingResult:
    price: Decimal
    delta: Decimal
    gamma: Decimal
    theta: Decimal
    vega: Decimal
    rho: Decimal
    model: PricingModel


@dataclass(frozen=True, slots=True)
class ImpliedVolResult:
    volatility: Decimal | None
    status: SolverStatus
    iterations: int
    price_error: Decimal | None
    lower_price_bound: Decimal
    upper_price_bound: Decimal
    model: PricingModel

    @property
    def converged(self) -> bool:
        return self.status is SolverStatus.CONVERGED

