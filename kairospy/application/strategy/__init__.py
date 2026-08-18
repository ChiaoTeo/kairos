"""Strategy runtime application facade.

User-authored strategy code must import its contract from ``kairospy.strategy``.
This package is for runtime composition and lifecycle control.
"""

from .application import (
    DecisionEffectEvaluation,
    DecisionHorizon,
    DecisionLifecycle,
    EffectEvidence,
    Strategy,
    StrategyDecision,
    StrategyDecisionApplication,
    StrategyEntrypoint,
    StrategyApplication,
    StrategyStatus,
    load_strategy,
)
from .domain.lifecycle import StrategyLifecycle

__all__ = [
    "Strategy",
    "DecisionEffectEvaluation",
    "DecisionHorizon",
    "DecisionLifecycle",
    "EffectEvidence",
    "StrategyDecision",
    "StrategyDecisionApplication",
    "StrategyEntrypoint",
    "StrategyApplication",
    "StrategyStatus",
    "StrategyLifecycle",
    "load_strategy",
]
