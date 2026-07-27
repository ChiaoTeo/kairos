from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import AsyncIterator

from kairospy.core.account import Environment
from kairospy.modes.backtest import BacktestEngine, BacktestResult
from kairospy.runtime import (
    AsyncEventSource,
    ModeRunner,
    RuntimeDataEnvelope,
    RuntimeMode,
    account_baseline_event,
    close_async_iterator,
)


class PaperEngine:
    runtime_mode = RuntimeMode.PAPER

    def __init__(self, *args, **kwargs) -> None:
        self._engine = BacktestEngine(*args, **kwargs)
        self._engine.runtime_mode = self.runtime_mode
        self._engine.account = replace(self._engine.account, environment=Environment.PAPER)

    def run(self, *args, **kwargs) -> BacktestResult:
        return self._engine.run(*args, **kwargs)


class StreamingPaperEngine:
    runtime_mode = RuntimeMode.PAPER

    def __init__(self, *args, **kwargs) -> None:
        self._engine = BacktestEngine(*args, **kwargs)
        self._engine.runtime_mode = self.runtime_mode
        self._engine.account = replace(self._engine.account, environment=Environment.PAPER)

    async def run(self, source: AsyncEventSource) -> BacktestResult:
        iterator = source.events()
        try:
            first_event = await _first_async_event(iterator)
            first_time = first_event.time if first_event is not None else datetime.now(timezone.utc)
            self._engine._deposit_initial_cash(first_time)
            runner = ModeRunner(
                self._engine.strategy,
                self._engine.data,
                self._engine.account.context,
                self.runtime_mode,
                equity_currency=self._engine.account.cash_currency,
                initial_equity=self._engine.account.initial_cash,
                market_resolver=self._engine.market_resolver,
            )
            run = await runner.run_async(
                _PrefixedAsyncEventSource(first_event, iterator),
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
                started_at=first_time,
                intent_handler=self._engine._handle_intents,
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
            metrics=self._engine.metrics_model.evaluate(
                tuple(self._engine._equity_curve),
                tuple(self._engine._trades),
                initial_equity=self._engine.account.initial_cash,
            ),
            coordinator=self._engine.coordinator,
            account_view=run.account_view,
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


__all__ = ["PaperEngine", "StreamingPaperEngine"]
