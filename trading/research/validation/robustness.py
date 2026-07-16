from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True,slots=True)
class RobustnessResult:
    parameter_stable: bool
    regime_stable: bool
    stress_cost_passed: bool
    parameter_success_fraction: float
    regime_success_fraction: float
    worst_stress_metric: float
    reasons: tuple[str,...]


def assess_robustness(parameter_metrics,regime_metrics,stress_metrics,*,minimum_parameter_fraction: float=.7,
                      minimum_regime_fraction: float=.6,minimum_stress_metric: float=0) -> RobustnessResult:
    if not parameter_metrics or not regime_metrics or not stress_metrics:raise ValueError("parameter, regime, and stress results are required")
    parameter_fraction=sum(value>0 for value in parameter_metrics)/len(parameter_metrics)
    regime_fraction=sum(value>0 for value in regime_metrics)/len(regime_metrics);worst=min(stress_metrics)
    flags=(parameter_fraction>=minimum_parameter_fraction,regime_fraction>=minimum_regime_fraction,worst>=minimum_stress_metric)
    names=("parameter_instability","regime_instability","stress_cost_failure")
    return RobustnessResult(*flags,parameter_fraction,regime_fraction,worst,tuple(name for name,passed in zip(names,flags) if not passed))
