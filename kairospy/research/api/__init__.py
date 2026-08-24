"""Stable contracts for user-authored Research code."""

from __future__ import annotations

from .protocol import (
    ResearchExperimentPolicy,
    ResearchPeriod,
    ResearchSpec,
)
from .experiments import BacktestBatchResult, BacktestCase, BacktestCaseResult


__all__ = [
    "BacktestBatchResult",
    "BacktestCase",
    "BacktestCaseResult",
    "ResearchExperimentPolicy",
    "ResearchPeriod",
    "ResearchSpec",
]
