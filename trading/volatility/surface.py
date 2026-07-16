from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from math import sqrt
from uuid import NAMESPACE_URL, uuid5

from trading.domain.identity import InstrumentId

from .calibration import calibrate_svi
from .models import ArbitrageDiagnostics, CalibrationStatus, SurfaceSnapshot, VolObservation
from .svi import total_variance


def build_surface(underlying_id: InstrumentId, as_of: datetime, observations: tuple[VolObservation, ...]) -> SurfaceSnapshot:
    if as_of.tzinfo is None:
        raise ValueError("surface as_of must be timezone-aware")
    rejected = []
    candidates = []
    for item in observations:
        reason = None
        if item.underlying_id != underlying_id:
            reason = "underlying_mismatch"
        elif item.as_of > as_of:
            reason = "future_observation"
        elif item.time_to_expiry <= 0:
            reason = "non_positive_maturity"
        elif item.implied_volatility <= 0:
            reason = "non_positive_iv"
        elif item.forward <= 0 or item.strike <= 0 or item.market_price < 0:
            reason = "invalid_market_input"
        if reason:
            rejected.append(f"{item.instrument_id.value}:{reason}")
        else:
            candidates.append(item)
    eligible = tuple(sorted(
        candidates,
        key=lambda item: (item.expiry, item.strike, item.right.value, item.instrument_id.value),
    ))
    payload = [
        [item.instrument_id.value, item.as_of.isoformat(), item.expiry.isoformat(), str(item.strike), str(item.forward), str(item.market_price), str(item.implied_volatility)]
        for item in eligible
    ]
    input_hash = sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()
    expiries = sorted({item.expiry for item in eligible})
    smiles = tuple(calibrate_svi(expiry, eligible) for expiry in expiries)
    diagnostics = diagnose_surface(smiles)
    calibrated_count = sum(item.status is CalibrationStatus.CALIBRATED for item in smiles)
    status = CalibrationStatus.CALIBRATED if calibrated_count else CalibrationStatus.INSUFFICIENT_DATA if smiles else CalibrationStatus.FAILED
    surface_id = str(uuid5(NAMESPACE_URL, f"{underlying_id.value}:{as_of.isoformat()}:{input_hash}:svi-v1"))
    return SurfaceSnapshot(surface_id, underlying_id, as_of, "svi", "1", input_hash, smiles, diagnostics, status, tuple(rejected))


def surface_implied_volatility(surface: SurfaceSnapshot, expiry: datetime, log_moneyness: Decimal) -> Decimal:
    calibrated = [item for item in surface.smiles if item.status is CalibrationStatus.CALIBRATED and item.parameters]
    if not calibrated:
        raise LookupError("surface contains no calibrated smiles")
    exact = next((item for item in calibrated if item.expiry == expiry), None)
    if exact:
        return Decimal(str(sqrt(float(total_variance(log_moneyness, exact.parameters) / exact.time_to_expiry))))
    before = [item for item in calibrated if item.expiry < expiry]
    after = [item for item in calibrated if item.expiry > expiry]
    if not before or not after:
        raise LookupError("surface does not bracket requested expiry")
    left, right = before[-1], after[0]
    target_t = left.time_to_expiry + (right.time_to_expiry - left.time_to_expiry) * Decimal(str((expiry - left.expiry).total_seconds() / (right.expiry - left.expiry).total_seconds()))
    weight = (target_t - left.time_to_expiry) / (right.time_to_expiry - left.time_to_expiry)
    variance = total_variance(log_moneyness, left.parameters) + weight * (total_variance(log_moneyness, right.parameters) - total_variance(log_moneyness, left.parameters))
    return Decimal(str(sqrt(float(variance / target_t))))


def diagnose_surface(smiles) -> ArbitrageDiagnostics:
    non_positive, butterfly, calendar = [], [], []
    grid = tuple(Decimal(index) / Decimal("20") for index in range(-20, 21))
    calibrated = [item for item in smiles if item.status is CalibrationStatus.CALIBRATED and item.parameters]
    for smile in calibrated:
        values = [total_variance(k, smile.parameters) for k in grid]
        non_positive.extend(f"{smile.expiry.isoformat()}:{k}" for k, value in zip(grid, values) if value <= 0)
        butterfly.extend(
            f"{smile.expiry.isoformat()}:{k}"
            for k in grid
            if _svi_density_condition(k, smile.parameters) < Decimal("-0.000001")
        )
    for left, right in zip(calibrated, calibrated[1:]):
        calendar.extend(
            f"{left.expiry.isoformat()}->{right.expiry.isoformat()}:{k}"
            for k in grid
            if total_variance(k, right.parameters) + Decimal("0.000001") < total_variance(k, left.parameters)
        )
    return ArbitrageDiagnostics(tuple(non_positive), tuple(butterfly), tuple(calendar))


def _svi_density_condition(log_moneyness, parameters):
    """Gatheral-Jacquier g(k); non-negative values imply no butterfly arbitrage."""
    from math import sqrt
    k = float(log_moneyness)
    a, b, rho, m, sigma = map(float, (parameters.a, parameters.b, parameters.rho, parameters.m, parameters.sigma))
    shifted = k - m
    root = sqrt(shifted * shifted + sigma * sigma)
    variance = a + b * (rho * shifted + root)
    if variance <= 0:
        return Decimal("-Infinity")
    first = b * (rho + shifted / root)
    second = b * sigma * sigma / (root * root * root)
    value = (1.0 - k * first / (2.0 * variance)) ** 2 - (first * first / 4.0) * (1.0 / variance + 0.25) + second / 2.0
    return Decimal(str(value))
