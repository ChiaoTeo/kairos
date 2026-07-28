from __future__ import annotations

from kairospy.application.context import DataContext
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.reference import MarketResolver
from kairospy.application.runtime.model import RuntimeMode
from kairospy.application.runtime.source import EventSource
from kairospy.application.service.domains.execution import (
    CommissionModel,
    FillModel,
    SimulatedAccount,
    SimulatedRunAdapter,
    SlippageModel,
)
from kairospy.application.strategy import Strategy

from .metrics import MetricsModel
from .result import BacktestResult


class BacktestEngine:
    runtime_mode = RuntimeMode.BACKTEST

    def __init__(
        self,
        strategy: Strategy,
        data: DataContext,
        account: SimulatedAccount,
        *,
        coordinator: ExecutionCoordinator | None = None,
        fill_model: FillModel | None = None,
        slippage_model: SlippageModel | None = None,
        commission_model: CommissionModel | None = None,
        metrics_model: MetricsModel | None = None,
        market_resolver: MarketResolver | None = None,
        account_journal: object | None = None,
        runtime_mode: RuntimeMode | None = None,
    ) -> None:
        self.runtime_mode = runtime_mode or self.runtime_mode
        self.metrics_model = metrics_model or MetricsModel()
        self.adapter = SimulatedRunAdapter(
            strategy,
            data,
            account,
            coordinator=coordinator,
            fill_model=fill_model,
            slippage_model=slippage_model,
            commission_model=commission_model,
            market_resolver=market_resolver,
            account_journal=account_journal,
            runtime_mode=self.runtime_mode,
        )

    def run(self, source: EventSource) -> BacktestResult:
        artifacts = self.adapter.run(source)
        return BacktestResult(
            account=artifacts.account,
            initial_equity=artifacts.initial_equity,
            runtime=artifacts.runtime,
            equity_curve=artifacts.equity_curve,
            fills=artifacts.fills,
            trades=artifacts.trades,
            metrics=self.metrics_model.evaluate(
                artifacts.equity_curve,
                artifacts.trades,
                initial_equity=artifacts.initial_equity,
            ),
            coordinator=artifacts.coordinator,
            account_view=artifacts.account_view,
        )


__all__ = ["BacktestEngine"]
