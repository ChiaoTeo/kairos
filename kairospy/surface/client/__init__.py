"""User-facing Python client surface."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .data import DataCatalogClient, DataClient
from .project import Kairos
from .research import ResearchClient

if TYPE_CHECKING:
    from ...application.launch.application.semantics import OptionBacktestConstraints
    from ...application.launch.application.specs import BacktestResult, BacktestSpec


def __getattr__(name: str) -> Any:
    if name in {"BacktestResult", "BacktestSpec"}:
        from ...application.launch.application.specs import BacktestResult, BacktestSpec

        return {"BacktestResult": BacktestResult, "BacktestSpec": BacktestSpec}[name]
    if name == "OptionBacktestConstraints":
        from ...application.launch.application.semantics import (
            OptionBacktestConstraints,
        )

        return OptionBacktestConstraints
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BacktestResult",
    "BacktestSpec",
    "DataCatalogClient",
    "DataClient",
    "Kairos",
    "OptionBacktestConstraints",
    "ResearchClient",
]
