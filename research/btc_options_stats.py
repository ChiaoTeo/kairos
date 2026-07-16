from __future__ import annotations

import math
import random
import statistics


def finite(values):
    return [float(value) for value in values if isinstance(value, (int, float)) and math.isfinite(value)]


def block_bootstrap_ci(values, block: int, samples: int = 4000, seed: int = 20260715):
    values = finite(values)
    if not values:
        return [math.nan, math.nan]
    rng, n, means = random.Random(seed), len(values), []
    for _ in range(samples):
        draw = []
        while len(draw) < n:
            start = rng.randrange(n); draw.extend(values[(start+offset) % n] for offset in range(block))
        means.append(statistics.fmean(draw[:n]))
    means.sort()
    return [means[int(samples*0.025)], means[int(samples*0.975)]]


def hac_mean_t(values, lags: int):
    values = finite(values); n = len(values)
    if n < 3:
        return math.nan
    mean = statistics.fmean(values); residuals = [value-mean for value in values]
    long_run = sum(value*value for value in residuals)/n
    for lag in range(1, min(lags, n-1)+1):
        covariance = sum(residuals[index]*residuals[index-lag] for index in range(lag, n))/n
        long_run += 2*(1-lag/(lags+1))*covariance
    standard_error = math.sqrt(max(long_run, 0)/n)
    return mean/standard_error if standard_error else math.nan


def percentile(values, quantile):
    values = sorted(finite(values)); position = (len(values)-1)*quantile
    lower, upper = math.floor(position), math.ceil(position)
    return values[lower] if lower == upper else values[lower]*(upper-position)+values[upper]*(position-lower)
