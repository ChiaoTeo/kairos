from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from math import erf, exp, log, pi, sqrt

from trading.domain.product import OptionRight

from .models import PricingInput, PricingModel, PricingResult


SQRT_TWO = sqrt(2.0)
SQRT_TWO_PI = sqrt(2.0 * pi)


def _cdf(value: float) -> float:
    return 0.5 * (1.0 + erf(value / SQRT_TWO))


def _pdf(value: float) -> float:
    return exp(-0.5 * value * value) / SQRT_TWO_PI


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def black_scholes(inputs: PricingInput) -> PricingResult:
    """European option value and annualized analytic Greeks.

    Delta and gamma are with respect to spot. Vega is the value change for a
    1.00 absolute volatility change, and theta is the value change per year.
    """
    s, k = float(inputs.underlying), float(inputs.strike)
    t, r = float(inputs.time_to_expiry), float(inputs.risk_free_rate)
    q, sigma = float(inputs.dividend_yield), float(inputs.volatility)
    if t == 0.0:
        intrinsic = max(0.0, s - k) if inputs.right is OptionRight.CALL else max(0.0, k - s)
        delta = (1.0 if s > k else 0.0) if inputs.right is OptionRight.CALL else (-1.0 if s < k else 0.0)
        return PricingResult(_decimal(intrinsic), _decimal(delta), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), PricingModel.BLACK_SCHOLES)
    if sigma == 0.0:
        forward_pv = s * exp(-q * t) - k * exp(-r * t)
        price = max(0.0, forward_pv) if inputs.right is OptionRight.CALL else max(0.0, -forward_pv)
        delta = exp(-q * t) * ((1.0 if forward_pv > 0 else 0.0) if inputs.right is OptionRight.CALL else (-1.0 if forward_pv < 0 else 0.0))
        return PricingResult(_decimal(price), _decimal(delta), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), PricingModel.BLACK_SCHOLES)

    root_t = sqrt(t)
    d1 = (log(s / k) + (r - q + 0.5 * sigma * sigma) * t) / (sigma * root_t)
    d2 = d1 - sigma * root_t
    discount_r, discount_q = exp(-r * t), exp(-q * t)
    density = _pdf(d1)
    gamma = discount_q * density / (s * sigma * root_t)
    vega = s * discount_q * density * root_t
    common_theta = -(s * discount_q * density * sigma) / (2.0 * root_t)
    if inputs.right is OptionRight.CALL:
        price = s * discount_q * _cdf(d1) - k * discount_r * _cdf(d2)
        delta = discount_q * _cdf(d1)
        theta = common_theta - r * k * discount_r * _cdf(d2) + q * s * discount_q * _cdf(d1)
        rho = k * t * discount_r * _cdf(d2)
    else:
        price = k * discount_r * _cdf(-d2) - s * discount_q * _cdf(-d1)
        delta = -discount_q * _cdf(-d1)
        theta = common_theta + r * k * discount_r * _cdf(-d2) - q * s * discount_q * _cdf(-d1)
        rho = -k * t * discount_r * _cdf(-d2)
    return PricingResult(*map(_decimal, (price, delta, gamma, theta, vega, rho)), PricingModel.BLACK_SCHOLES)


def black76(inputs: PricingInput) -> PricingResult:
    """Black-76 value using ``underlying`` as the forward price.

    Delta and gamma are derivatives with respect to the forward. Dividend yield
    is not used and must be zero to avoid an ambiguous input contract.
    """
    if inputs.dividend_yield != 0:
        raise ValueError("Black-76 does not accept a dividend yield")
    f, k = float(inputs.underlying), float(inputs.strike)
    t, r, sigma = float(inputs.time_to_expiry), float(inputs.risk_free_rate), float(inputs.volatility)
    discount = exp(-r * t)
    if t == 0.0:
        intrinsic = max(0.0, f - k) if inputs.right is OptionRight.CALL else max(0.0, k - f)
        delta = (1.0 if f > k else 0.0) if inputs.right is OptionRight.CALL else (-1.0 if f < k else 0.0)
        return PricingResult(_decimal(intrinsic), _decimal(delta), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), PricingModel.BLACK_76)
    if sigma == 0.0:
        intrinsic = max(0.0, f - k) if inputs.right is OptionRight.CALL else max(0.0, k - f)
        delta = discount * ((1.0 if f > k else 0.0) if inputs.right is OptionRight.CALL else (-1.0 if f < k else 0.0))
        return PricingResult(_decimal(discount * intrinsic), _decimal(delta), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), PricingModel.BLACK_76)

    root_t = sqrt(t)
    d1 = (log(f / k) + 0.5 * sigma * sigma * t) / (sigma * root_t)
    d2 = d1 - sigma * root_t
    density = _pdf(d1)
    gamma = discount * density / (f * sigma * root_t)
    vega = discount * f * density * root_t
    if inputs.right is OptionRight.CALL:
        undiscounted = f * _cdf(d1) - k * _cdf(d2)
        delta = discount * _cdf(d1)
    else:
        undiscounted = k * _cdf(-d2) - f * _cdf(-d1)
        delta = -discount * _cdf(-d1)
    price = discount * undiscounted
    # Theta includes discount carry and time decay with F held constant.
    theta = r * price - discount * f * density * sigma / (2.0 * root_t)
    rho = -t * price
    return PricingResult(*map(_decimal, (price, delta, gamma, theta, vega, rho)), PricingModel.BLACK_76)


def price_with_volatility(inputs: PricingInput, volatility: Decimal, model: PricingModel) -> PricingResult:
    updated = replace(inputs, volatility=volatility)
    if model is PricingModel.BLACK_SCHOLES:
        return black_scholes(updated)
    if model is PricingModel.BLACK_76:
        return black76(updated)
    raise ValueError(f"unsupported pricing model: {model}")

