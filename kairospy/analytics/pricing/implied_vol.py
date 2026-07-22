from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from math import exp

from kairospy.reference.contracts import OptionRight

from .black import price_with_volatility
from .option_pricing_contracts import ImpliedVolResult, PricingInput, PricingModel, SolverStatus


def price_bounds(inputs: PricingInput, model: PricingModel) -> tuple[Decimal, Decimal]:
    t, r = float(inputs.time_to_expiry), float(inputs.risk_free_rate)
    discount_r = Decimal(str(exp(-r * t)))
    if model is PricingModel.BLACK_76:
        discounted_forward = discount_r * inputs.underlying
        discounted_strike = discount_r * inputs.strike
        upper = discounted_forward if inputs.right is OptionRight.CALL else discounted_strike
    else:
        discount_q = Decimal(str(exp(-float(inputs.dividend_yield) * t)))
        discounted_forward = discount_q * inputs.underlying
        discounted_strike = discount_r * inputs.strike
        upper = discounted_forward if inputs.right is OptionRight.CALL else discounted_strike
    intrinsic = discounted_forward - discounted_strike
    if inputs.right is OptionRight.PUT:
        intrinsic = -intrinsic
    return max(Decimal("0"), intrinsic), upper


def implied_volatility(
    market_price: Decimal,
    inputs: PricingInput,
    model: PricingModel,
    *,
    minimum_volatility: Decimal = Decimal("0.000001"),
    maximum_volatility: Decimal = Decimal("5"),
    price_tolerance: Decimal = Decimal("0.00000001"),
    volatility_tolerance: Decimal = Decimal("0.00000001"),
    max_iterations: int = 200,
) -> ImpliedVolResult:
    if market_price < 0:
        raise ValueError("market price cannot be negative")
    if inputs.time_to_expiry <= 0:
        raise ValueError("implied volatility requires positive time to expiry")
    if minimum_volatility <= 0 or maximum_volatility <= minimum_volatility:
        raise ValueError("invalid volatility bracket")
    lower_bound, upper_bound = price_bounds(inputs, model)
    if market_price < lower_bound - price_tolerance or market_price > upper_bound + price_tolerance:
        return ImpliedVolResult(None, SolverStatus.PRICE_OUT_OF_BOUNDS, 0, None, lower_bound, upper_bound, model)

    low, high = minimum_volatility, maximum_volatility
    low_price = price_with_volatility(inputs, low, model).price
    high_price = price_with_volatility(inputs, high, model).price
    if market_price < low_price - price_tolerance or market_price > high_price + price_tolerance:
        return ImpliedVolResult(None, SolverStatus.NOT_BRACKETED, 0, None, lower_bound, upper_bound, model)
    error = None
    for iteration in range(1, max_iterations + 1):
        middle = (low + high) / 2
        calculated = price_with_volatility(inputs, middle, model).price
        error = calculated - market_price
        if abs(error) <= price_tolerance or high - low <= volatility_tolerance:
            return ImpliedVolResult(middle, SolverStatus.CONVERGED, iteration, error, lower_bound, upper_bound, model)
        if error < 0:
            low = middle
        else:
            high = middle
    return ImpliedVolResult(None, SolverStatus.MAX_ITERATIONS, max_iterations, error, lower_bound, upper_bound, model)

