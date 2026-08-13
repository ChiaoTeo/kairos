"""Public Python SDK for Kairos projects, research, and strategies."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .surface.client import DataClient, Kairos, ResearchClient
from .application.data import (
    DataAcquisitionPlan,
    DataRequirement,
    DataUnavailableError,
    DataTrustGateReport,
    DatasetDescription,
    DatasetReadPlan,
    DatasetRef,
    DatasetSetRef,
    OptionMarketDataTarget,
)
from .research import (
    ResearchExperimentPolicy,
    ResearchPeriod,
    ResearchSpec,
)

if TYPE_CHECKING:
    from .surface.client import (
        BacktestResult,
        BacktestSpec,
        OptionBacktestConstraints,
    )
    from .research import BacktestBatchResult, BacktestCase, BacktestCaseResult


def __getattr__(name: str) -> Any:
    """Load runtime capabilities only when that public surface is requested."""

    if name in {
        "BacktestResult",
        "BacktestSpec",
        "OptionBacktestConstraints",
    }:
        from .surface.client import (
            BacktestResult,
            BacktestSpec,
            OptionBacktestConstraints,
        )

        return {
            "BacktestResult": BacktestResult,
            "BacktestSpec": BacktestSpec,
            "OptionBacktestConstraints": OptionBacktestConstraints,
        }[name]
    if name in {"BacktestBatchResult", "BacktestCase", "BacktestCaseResult"}:
        from .research import BacktestBatchResult, BacktestCase, BacktestCaseResult

        return {
            "BacktestBatchResult": BacktestBatchResult,
            "BacktestCase": BacktestCase,
            "BacktestCaseResult": BacktestCaseResult,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DataAcquisitionPlan",
    "DataClient",
    "BacktestBatchResult",
    "BacktestCase",
    "BacktestCaseResult",
    "BacktestSpec",
    "BacktestResult",
    "DataRequirement",
    "DataUnavailableError",
    "DataTrustGateReport",
    "DatasetDescription",
    "DatasetReadPlan",
    "DatasetRef",
    "DatasetSetRef",
    "Kairos",
    "OptionMarketDataTarget",
    "OptionBacktestConstraints",
    "ResearchClient",
    "ResearchExperimentPolicy",
    "ResearchPeriod",
    "ResearchSpec",
]
