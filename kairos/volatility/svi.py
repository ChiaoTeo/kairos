from __future__ import annotations

from decimal import Decimal
from math import sqrt

from .contracts import SviParameters


def total_variance(log_moneyness: Decimal, parameters: SviParameters) -> Decimal:
    k = float(log_moneyness)
    a, b, rho, m, sigma = map(float, (parameters.a, parameters.b, parameters.rho, parameters.m, parameters.sigma))
    shifted = k - m
    return Decimal(str(a + b * (rho * shifted + sqrt(shifted * shifted + sigma * sigma))))


def implied_volatility(log_moneyness: Decimal, time_to_expiry: Decimal, parameters: SviParameters) -> Decimal:
    if time_to_expiry <= 0:
        raise ValueError("time to expiry must be positive")
    variance = total_variance(log_moneyness, parameters)
    if variance <= 0:
        raise ValueError("SVI produced non-positive total variance")
    return Decimal(str(sqrt(float(variance / time_to_expiry))))

