"""Market-owned deterministic analytical projections.

This module receives canonical numeric values at the Application boundary. It
does not decode wire/persistence decimal representations; those conversions
remain owned by their lower boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping


_YEAR_NANOS = 365.25 * 86_400 * 1_000_000_000
_SQRT_TWO = math.sqrt(2.0)
_SQRT_TWO_PI = math.sqrt(2.0 * math.pi)


@dataclass(frozen=True, slots=True)
class OptionGreeksProjectionRequest:
    market_id: str
    instrument_id: str
    option_right: str
    expiry_unix_nanos: int
    strike: float
    underlying_price: float
    option_price: float
    observed_at_unix_nanos: int
    available_at_unix_nanos: int
    risk_free_rate: float
    dividend_yield: float = 0.0
    price_basis: str = "mid"
    source_id: str = "kairos-derived"
    reference_snapshot_id: str | None = None

    def __post_init__(self) -> None:
        right = self.option_right.upper()
        if right not in {"C", "P"}:
            raise ValueError("option_right must be C or P")
        object.__setattr__(self, "option_right", right)
        for name in ("market_id", "instrument_id", "price_basis", "source_id"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if self.expiry_unix_nanos <= self.observed_at_unix_nanos:
            raise ValueError("option expiry must be after the observation")
        if self.available_at_unix_nanos < self.observed_at_unix_nanos:
            raise ValueError("option input cannot be available before it was observed")
        if self.strike <= 0 or self.underlying_price <= 0 or self.option_price <= 0:
            raise ValueError(
                "strike, underlying price and option price must be positive"
            )


@dataclass(frozen=True, slots=True)
class OptionGreeksProjectionResult:
    implied_volatility: float
    delta: float
    gamma: float
    vega: float
    theta: float
    event: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class MarketAnalyticalApplication:
    """Build provider-independent Market analytical facts."""

    model_version: str = "black-scholes-european-v1"

    def option_greeks(
        self, request: OptionGreeksProjectionRequest
    ) -> OptionGreeksProjectionResult:
        time_to_expiry = (
            request.expiry_unix_nanos - request.observed_at_unix_nanos
        ) / _YEAR_NANOS
        volatility = _implied_volatility(request, time_to_expiry)
        delta, gamma, vega, theta = _greeks(request, time_to_expiry, volatility)
        value = {
            "market_id": request.market_id,
            "instrument_id": request.instrument_id,
            "expiry_unix_nanos": request.expiry_unix_nanos,
            "strike": _number(request.strike),
            "option_right": request.option_right,
            "delta": _number(delta),
            "gamma": _number(gamma),
            "vega": _number(vega),
            "theta": _number(theta),
            "implied_volatility": _number(volatility),
            "underlying_price": _number(request.underlying_price),
            "option_price": _number(request.option_price),
            "price_basis": request.price_basis,
            "risk_free_rate": _number(request.risk_free_rate),
            "dividend_yield": _number(request.dividend_yield),
            "observed_at_unix_nanos": request.observed_at_unix_nanos,
            "available_at_unix_nanos": request.available_at_unix_nanos,
            "source_id": request.source_id,
            "derivation": self.model_version,
            "reference_snapshot_id": request.reference_snapshot_id,
            "model_semantics": {
                "exercise": "european-proxy",
                "theta_basis": "annual",
                "vega_basis": "volatility-one-point-zero",
                "calendar_days_per_year": 365.25,
            },
        }
        return OptionGreeksProjectionResult(
            implied_volatility=volatility,
            delta=delta,
            gamma=gamma,
            vega=vega,
            theta=theta,
            event={"Greeks": value},
        )


def _implied_volatility(
    request: OptionGreeksProjectionRequest, time_to_expiry: float
) -> float:
    discounted_spot = request.underlying_price * math.exp(
        -request.dividend_yield * time_to_expiry
    )
    discounted_strike = request.strike * math.exp(
        -request.risk_free_rate * time_to_expiry
    )
    if request.option_right == "C":
        lower = max(0.0, discounted_spot - discounted_strike)
        upper = discounted_spot
    else:
        lower = max(0.0, discounted_strike - discounted_spot)
        upper = discounted_strike
    if not lower <= request.option_price <= upper:
        raise ValueError("option price violates discounted no-arbitrage bounds")
    low = 1e-6
    high = 5.0
    low_price = _price(request, time_to_expiry, low)
    high_price = _price(request, time_to_expiry, high)
    if not low_price <= request.option_price <= high_price:
        raise ValueError("option price has no implied volatility in [1e-6, 5]")
    for _ in range(100):
        middle = (low + high) / 2.0
        value = _price(request, time_to_expiry, middle)
        if value < request.option_price:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def _price(
    request: OptionGreeksProjectionRequest,
    time_to_expiry: float,
    volatility: float,
) -> float:
    d1, d2 = _d1_d2(request, time_to_expiry, volatility)
    spot = request.underlying_price * math.exp(-request.dividend_yield * time_to_expiry)
    strike = request.strike * math.exp(-request.risk_free_rate * time_to_expiry)
    if request.option_right == "C":
        return spot * _cdf(d1) - strike * _cdf(d2)
    return strike * _cdf(-d2) - spot * _cdf(-d1)


def _greeks(
    request: OptionGreeksProjectionRequest,
    time_to_expiry: float,
    volatility: float,
) -> tuple[float, float, float, float]:
    d1, d2 = _d1_d2(request, time_to_expiry, volatility)
    discount_dividend = math.exp(-request.dividend_yield * time_to_expiry)
    discount_rate = math.exp(-request.risk_free_rate * time_to_expiry)
    density = _pdf(d1)
    root_time = math.sqrt(time_to_expiry)
    gamma = (
        discount_dividend
        * density
        / (request.underlying_price * volatility * root_time)
    )
    vega = request.underlying_price * discount_dividend * density * root_time
    common_theta = -(
        request.underlying_price
        * discount_dividend
        * density
        * volatility
        / (2.0 * root_time)
    )
    if request.option_right == "C":
        delta = discount_dividend * _cdf(d1)
        theta = (
            common_theta
            - request.risk_free_rate * request.strike * discount_rate * _cdf(d2)
            + request.dividend_yield
            * request.underlying_price
            * discount_dividend
            * _cdf(d1)
        )
    else:
        delta = discount_dividend * (_cdf(d1) - 1.0)
        theta = (
            common_theta
            + request.risk_free_rate * request.strike * discount_rate * _cdf(-d2)
            - request.dividend_yield
            * request.underlying_price
            * discount_dividend
            * _cdf(-d1)
        )
    return delta, gamma, vega, theta


def _d1_d2(
    request: OptionGreeksProjectionRequest,
    time_to_expiry: float,
    volatility: float,
) -> tuple[float, float]:
    root_time = math.sqrt(time_to_expiry)
    d1 = (
        math.log(request.underlying_price / request.strike)
        + (
            request.risk_free_rate
            - request.dividend_yield
            + volatility * volatility / 2.0
        )
        * time_to_expiry
    ) / (volatility * root_time)
    return d1, d1 - volatility * root_time


def _cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / _SQRT_TWO))


def _pdf(value: float) -> float:
    return math.exp(-0.5 * value * value) / _SQRT_TWO_PI


def _number(value: float) -> str:
    return format(value, ".15g")
