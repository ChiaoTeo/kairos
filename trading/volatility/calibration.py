from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from math import sqrt

from .models import CalibrationStatus, SmileCalibration, SviParameters, VolObservation
from .svi import total_variance


def calibrate_svi(expiry: datetime, observations: tuple[VolObservation, ...]) -> SmileCalibration:
    """Deterministic bounded coordinate-search SVI calibration.

    This dependency-free optimizer is deliberately conservative. Its result and
    diagnostics are stable across runs; a future SciPy optimizer can implement
    the same contract without changing strategy or backtest interfaces.
    """
    points = tuple(item for item in observations if item.expiry == expiry and item.time_to_expiry > 0)
    if len(points) < 5:
        return SmileCalibration(expiry, points[0].time_to_expiry if points else Decimal("0"), points[0].forward if points else Decimal("0"), None, CalibrationStatus.INSUFFICIENT_DATA, len(points), None)
    x = [float(item.log_moneyness) for item in points]
    y = [float(item.total_variance) for item in points]
    minimum = max(1e-8, min(y))
    params = [minimum * 0.8, max(0.01, (max(y) - min(y)) / max(0.05, max(x) - min(x))), 0.0, 0.0, 0.1]
    bounds = [(1e-10, max(y) * 2), (1e-8, 5.0), (-0.999, 0.999), (min(x) - 1, max(x) + 1), (0.001, 2.0)]
    steps = [max(minimum * 0.25, 0.0001), 0.05, 0.1, 0.05, 0.05]

    def loss(values: list[float]) -> float:
        a, b, rho, m, sigma = values
        errors = []
        for k, observed in zip(x, y):
            shifted = k - m
            fitted = a + b * (rho * shifted + sqrt(shifted * shifted + sigma * sigma))
            errors.append((fitted - observed) ** 2)
        return sum(errors) / len(errors)

    best = loss(params)
    for _ in range(250):
        improved = False
        for index, step in enumerate(steps):
            for direction in (-1.0, 1.0):
                candidate = list(params)
                candidate[index] = min(bounds[index][1], max(bounds[index][0], candidate[index] + direction * step))
                score = loss(candidate)
                if score < best:
                    params, best, improved = candidate, score, True
        if not improved:
            steps = [value / 2.0 for value in steps]
            if max(steps) < 1e-7:
                break
    fitted = SviParameters(*(Decimal(str(value)) for value in params))
    return SmileCalibration(expiry, points[0].time_to_expiry, points[0].forward, fitted, CalibrationStatus.CALIBRATED, len(points), Decimal(str(sqrt(best))))

