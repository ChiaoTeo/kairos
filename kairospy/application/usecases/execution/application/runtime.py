"""Runtime-facing assembly entry points for the execution usecase."""

from __future__ import annotations

from decimal import Decimal

from kairospy.application.usecases.execution.services.runtime.live import LiveExecutionService
from kairospy.application.usecases.execution.services.runtime.modes.backtest import BacktestExecutionService
from kairospy.application.usecases.execution.services.runtime.modes.paper import PaperExecutionService
from kairospy.application.usecases.execution.services.runtime.projections import RuntimeExecutionService, TradingRuntimeExecutionService
from kairospy.application.usecases.execution.services.runtime.simulated import SimulatedExecutionRuntimeService
from kairospy.application.usecases.execution.services.simulation import ImmediateFillModel, PercentageCommissionModel
from kairospy.application.usecases.execution.services.coordinator import ExecutionCoordinator
from .component import ExecutionApplication


def build_execution_coordinator() -> ExecutionCoordinator:
    return ExecutionCoordinator()


def build_backtest_runtime(coordinator: object, **kwargs: object) -> BacktestExecutionService:
    return BacktestExecutionService(coordinator, **kwargs)


def build_paper_runtime(coordinator: object, **kwargs: object) -> PaperExecutionService:
    return PaperExecutionService(coordinator, **kwargs)


def build_live_runtime(**kwargs: object) -> LiveExecutionService:
    return LiveExecutionService(**kwargs)


def build_simulated_runtime(coordinator: object, **kwargs: object) -> SimulatedExecutionRuntimeService:
    return SimulatedExecutionRuntimeService(coordinator, **kwargs)


def build_immediate_fill_model(**kwargs: object) -> object:
    return ImmediateFillModel(**kwargs)


def build_percentage_commission_model(rate: Decimal) -> object:
    return PercentageCommissionModel(rate)


def execution_runtime_adapters(application: ExecutionApplication):
    """Return private handlers needed only by runtime composition.

    This is an assembly hook, not a business API.  Business callers must use
    ``ExecutionApplication`` directly.
    """
    return application.runtime_adapters()

__all__ = [
    "RuntimeExecutionService",
    "TradingRuntimeExecutionService",
    "build_execution_coordinator",
    "build_backtest_runtime",
    "build_immediate_fill_model",
    "build_live_runtime",
    "build_percentage_commission_model",
    "build_paper_runtime",
    "build_simulated_runtime",
    "execution_runtime_adapters",
]
