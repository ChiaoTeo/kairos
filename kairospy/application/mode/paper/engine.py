from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import AsyncIterator

from kairospy.core.account import Environment
from kairospy.application.context import DataContext
from kairospy.application.mode.backtest import BacktestResult
from kairospy.application.mode.backtest import SimulatedAccount
from kairospy.application.runtime.model import PAPER_PROFILE, RuntimeDataEnvelope, RuntimeMode
from kairospy.application.runtime.projection.account import AccountCurrentProjection
from kairospy.application.runtime.run import (
    RuntimeAsyncEnvelopeBridge,
    RuntimeProjectionConfig,
    RuntimeRunSpec,
    RuntimeServiceConfig,
    RuntimeStateConfig,
)
from kairospy.application.runtime.source import AsyncEventSource, close_async_iterator
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.reference import MarketResolver
from kairospy.application.service.domains.account import account_baseline_event
from kairospy.application.service.domains.execution import CommissionModel, FillModel, SimulatedRunAdapter, SlippageModel
from kairospy.application.strategy import Strategy

from kairospy.application.mode.backtest.metrics import MetricsModel


class PaperEngine:
    runtime_mode = RuntimeMode.PAPER

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
    ) -> None:
        self._metrics_model = metrics_model or MetricsModel()
        self._engine = SimulatedRunAdapter(
            strategy,
            data,
            replace(account, environment=Environment.PAPER),
            coordinator=coordinator,
            fill_model=fill_model,
            slippage_model=slippage_model,
            commission_model=commission_model,
            market_resolver=market_resolver,
            account_journal=account_journal,
            runtime_mode=self.runtime_mode,
        )

    def run(self, *args, **kwargs) -> BacktestResult:
        artifacts = self._engine.run(*args, **kwargs)
        return _backtest_result_from_artifacts(artifacts, self._metrics_model)


class StreamingPaperEngine:
    runtime_mode = RuntimeMode.PAPER

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
    ) -> None:
        self._metrics_model = metrics_model or MetricsModel()
        self._engine = SimulatedRunAdapter(
            strategy,
            data,
            replace(account, environment=Environment.PAPER),
            coordinator=coordinator,
            fill_model=fill_model,
            slippage_model=slippage_model,
            commission_model=commission_model,
            market_resolver=market_resolver,
            account_journal=account_journal,
            runtime_mode=self.runtime_mode,
        )

    async def run(self, source: AsyncEventSource) -> BacktestResult:
        iterator = source.events()
        try:
            first_event = await _first_async_event(iterator)
            first_time = first_event.time if first_event is not None else datetime.now(timezone.utc)
            self._engine._deposit_initial_cash(first_time)
            account_projection = AccountCurrentProjection(
                self._engine.account.context,
                equity_currency=self._engine.account.cash_currency,
                initial_equity=self._engine.account.initial_cash,
            )
            run = await RuntimeAsyncEnvelopeBridge.run(
                RuntimeRunSpec(
                    run_id=self._engine.account.account_id,
                    profile=PAPER_PROFILE,
                    strategy=self._engine.strategy,
                    source=_PrefixedAsyncEventSource(first_event, iterator),
                    state_config=RuntimeStateConfig(self._engine.data, self._engine.market_resolver),
                    service_config=RuntimeServiceConfig(intent_handler=self._engine.handle_intents),
                    projection_config=RuntimeProjectionConfig((account_projection,)),
                    started_at=first_time,
                    pre_events=(
                        account_baseline_event(
                            self._engine.account.context,
                            sequence=self._engine._next_account_event_sequence(),
                            at=first_time,
                            currency=self._engine.account.cash_currency,
                            equity=self._engine.account.initial_cash,
                            metadata={"mode": self.runtime_mode.value},
                        ),
                    ),
                )
            )
        finally:
            await close_async_iterator(iterator)
        return BacktestResult(
            account=self._engine.account.context,
            initial_equity=self._engine.account.initial_cash,
            runtime=run.runtime,
            equity_curve=tuple(self._engine._equity_curve),
            fills=tuple(self._engine._fills),
            trades=tuple(self._engine._trades),
            metrics=self._metrics_model.evaluate(
                tuple(self._engine._equity_curve),
                tuple(self._engine._trades),
                initial_equity=self._engine.account.initial_cash,
            ),
            coordinator=self._engine.coordinator,
            account_view=run.views.require(account_projection.key),
        )


async def _first_async_event(events: AsyncIterator[RuntimeDataEnvelope]) -> RuntimeDataEnvelope | None:
    try:
        return await events.__anext__()
    except StopAsyncIteration:
        return None


class _PrefixedAsyncEventSource:
    def __init__(self, first: RuntimeDataEnvelope | None, events: AsyncIterator[RuntimeDataEnvelope]) -> None:
        self.first = first
        self._events = events

    async def events(self) -> AsyncIterator[RuntimeDataEnvelope]:
        try:
            if self.first is not None:
                yield self.first
            async for event in self._events:
                yield event
        finally:
            await close_async_iterator(self._events)


def _backtest_result_from_artifacts(artifacts, metrics_model: MetricsModel) -> BacktestResult:
    return BacktestResult(
        account=artifacts.account,
        initial_equity=artifacts.initial_equity,
        runtime=artifacts.runtime,
        equity_curve=artifacts.equity_curve,
        fills=artifacts.fills,
        trades=artifacts.trades,
        metrics=metrics_model.evaluate(
            artifacts.equity_curve,
            artifacts.trades,
            initial_equity=artifacts.initial_equity,
        ),
        coordinator=artifacts.coordinator,
        account_view=artifacts.account_view,
    )


__all__ = ["PaperEngine", "StreamingPaperEngine"]
