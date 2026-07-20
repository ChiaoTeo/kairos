from __future__ import annotations

"""Compatibility exports for synthetic backtest scenario helpers."""

from .synthetic_scenarios import (
    DatasetReadiness,
    SyntheticScenario,
    _put,
    assess_dataset,
    build_synthetic_backtest_dataset,
)

MockScenario = SyntheticScenario
make_mock_dataset = build_synthetic_backtest_dataset

__all__ = [
    "DatasetReadiness",
    "MockScenario",
    "SyntheticScenario",
    "_put",
    "assess_dataset",
    "build_synthetic_backtest_dataset",
    "make_mock_dataset",
]
