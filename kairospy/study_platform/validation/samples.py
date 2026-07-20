from __future__ import annotations

import math

from .contracts import SampleSufficiency


def overlap_adjusted_effective_samples(raw_observations: int, horizon_steps: int,
                                       autocorrelations: tuple[float, ...] = ()) -> float:
    if raw_observations < 0 or horizon_steps < 1:
        raise ValueError("raw observations must be non-negative and horizon positive")
    if raw_observations == 0: return 0.0
    overlap_ess = raw_observations / horizon_steps
    if not autocorrelations: return overlap_ess
    weighted = sum((1 - (lag + 1) / raw_observations) * rho for lag, rho in enumerate(autocorrelations) if lag + 1 < raw_observations)
    acf_ess = raw_observations / max(1.0, 1 + 2 * weighted)
    return max(0.0, min(float(raw_observations), overlap_ess, acf_ess))


def approximate_required_samples(standardized_effect: float, *, target_power: float = .80, alpha: float = .05) -> int:
    if standardized_effect <= 0: raise ValueError("standardized effect must be positive")
    if not 0 < target_power < 1 or not 0 < alpha < 1: raise ValueError("power and alpha must be in (0, 1)")
    return math.ceil(((_normal_quantile(1-alpha/2) + _normal_quantile(target_power)) / standardized_effect) ** 2)


def assess_sample_sufficiency(raw_observations: int, horizon_steps: int, standardized_effect: float, *,
                              target_power: float = .80, alpha: float = .05,
                              non_overlapping_observations: int | None = None,
                              regimes_observed: tuple[str, ...] = (), extreme_events: int = 0) -> SampleSufficiency:
    effective = overlap_adjusted_effective_samples(raw_observations, horizon_steps)
    required = approximate_required_samples(standardized_effect, target_power=target_power, alpha=alpha)
    return SampleSufficiency(raw_observations, non_overlapping_observations if non_overlapping_observations is not None else raw_observations//horizon_steps,
                             effective, required, standardized_effect, target_power, regimes_observed, extreme_events)


def _normal_quantile(p: float) -> float:
    if not 0 < p < 1: raise ValueError("probability must be in (0, 1)")
    a=(-39.69683028665376,220.9460984245205,-275.9285104469687,138.3577518672690,-30.66479806614716,2.506628277459239)
    b=(-54.47609879822406,161.5858368580409,-155.6989798598866,66.80131188771972,-13.28068155288572)
    c=(-.007784894002430293,-.3223964580411365,-2.400758277161838,-2.549732539343734,4.374664141464968,2.938163982698783)
    d=(.007784695709041462,.3224671290700398,2.445134137142996,3.754408661907416)
    if p < .02425:
        q=math.sqrt(-2*math.log(p)); return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p <= .97575:
        q=p-.5;r=q*q;return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q/(((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    q=math.sqrt(-2*math.log(1-p));return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
