from __future__ import annotations

from decimal import Decimal
from math import exp

from kairospy.market.types import RateCurve


def zero_rate(curve: RateCurve, maturity_years: Decimal) -> Decimal:
    """Linearly interpolate zero rates and flat-extrapolate curve ends."""
    if maturity_years < 0:
        raise ValueError("maturity cannot be negative")
    if maturity_years <= curve.nodes[0].maturity_years:
        return curve.nodes[0].zero_rate
    if maturity_years >= curve.nodes[-1].maturity_years:
        return curve.nodes[-1].zero_rate
    for left, right in zip(curve.nodes, curve.nodes[1:]):
        if left.maturity_years <= maturity_years <= right.maturity_years:
            weight = (maturity_years - left.maturity_years) / (right.maturity_years - left.maturity_years)
            return left.zero_rate + weight * (right.zero_rate - left.zero_rate)
    raise RuntimeError("ordered rate curve did not bracket maturity")


def cost_of_carry_forward(
    spot: Decimal,
    maturity_years: Decimal,
    rate: Decimal,
    dividend_yield: Decimal = Decimal("0"),
) -> Decimal:
    if spot <= 0 or maturity_years < 0:
        raise ValueError("spot must be positive and maturity non-negative")
    return spot * Decimal(str(exp(float((rate - dividend_yield) * maturity_years))))


def parity_forward(
    call_price: Decimal,
    put_price: Decimal,
    strike: Decimal,
    maturity_years: Decimal,
    rate: Decimal,
) -> Decimal:
    if call_price < 0 or put_price < 0 or strike <= 0 or maturity_years <= 0:
        raise ValueError("invalid put-call parity inputs")
    return strike + (call_price - put_price) * Decimal(str(exp(float(rate * maturity_years))))
