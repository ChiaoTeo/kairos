from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from tempfile import TemporaryDirectory
from typing import Protocol

from kairospy.context import DataContext, StrategyContext
from kairospy.core.market import MarketSubscription
from kairospy.core.reference import MarketResolver
from kairospy.data import DataStore
from kairospy.modes.backtest import BacktestResult, SimulatedAccount
from kairospy.runtime import AsyncEventSource, ModeRunner, RuntimeMode, account_baseline_event
from kairospy.strategy import Strategy


class PaperSourceController(Protocol):
    def update_subscriptions(
        self,
        subscriptions: tuple[MarketSubscription, ...],
        context: StrategyContext,
        hook: str,
    ) -> None:
        ...

    def source(self) -> AsyncEventSource:
        ...


@dataclass(frozen=True, slots=True)
class PaperAccountConfig:
    account_id: str = "paper"
    cash: Decimal | str | int | float = Decimal("10000")
    cash_currency: str = "USD"
    broker: str = "simulated"
    fee_rate: Decimal | str | int | float = Decimal("0")
    price_field: str = "close"

    def simulated_account(self) -> SimulatedAccount:
        return SimulatedAccount(
            self.account_id,
            Decimal(str(self.cash)),
            cash_currency=self.cash_currency,
            broker=self.broker,
            fee_rate=Decimal(str(self.fee_rate)),
            price_field=self.price_field,
        )


@dataclass(frozen=True, slots=True)
class StreamingPaperRun:
    strategy: Strategy
    source_controller: PaperSourceController
    account: PaperAccountConfig = PaperAccountConfig()
    market_resolver: MarketResolver | None = None
    storage_format: str = "jsonl"
    started_at: datetime | None = None
    account_journal: object | None = None

    async def run(self, *, data_directory: str | None = None) -> BacktestResult:
        if data_directory is not None:
            return await self._run_with_data_directory(data_directory)
        with TemporaryDirectory() as temporary:
            return await self._run_with_data_directory(temporary)

    async def _run_with_data_directory(self, data_directory: str) -> BacktestResult:
        data = DataContext(DataStore(data_directory, storage_format=self.storage_format))
        account = self.account.simulated_account()
        started_at = self.started_at or datetime.now(timezone.utc)
        from .engine import StreamingPaperEngine

        engine = StreamingPaperEngine(
            self.strategy,
            data,
            account,
            market_resolver=self.market_resolver,
            account_journal=self.account_journal,
        )
        engine._engine._deposit_initial_cash(started_at)
        runner = ModeRunner(
            engine._engine.strategy,
            engine._engine.data,
            engine._engine.account.context,
            RuntimeMode.PAPER,
            equity_currency=account.cash_currency,
            initial_equity=account.initial_cash,
            market_resolver=engine._engine.market_resolver,
        )
        session = runner.start_async(
            pre_events=(
                account_baseline_event(
                    engine._engine.account.context,
                    sequence=engine._engine._next_account_event_sequence(),
                    at=started_at,
                    currency=account.cash_currency,
                    equity=account.initial_cash,
                    metadata={"mode": RuntimeMode.PAPER.value},
                ),
            ),
            started_at=started_at,
            intent_handler=engine._engine._handle_intents,
            subscription_handler=self.source_controller.update_subscriptions,
        )
        run = await session.run_async(self.source_controller.source())
        return BacktestResult(
            account=engine._engine.account.context,
            initial_equity=account.initial_cash,
            runtime=run.runtime,
            equity_curve=tuple(engine._engine._equity_curve),
            fills=tuple(engine._engine._fills),
            trades=tuple(engine._engine._trades),
            metrics=engine._engine.metrics_model.evaluate(
                tuple(engine._engine._equity_curve),
                tuple(engine._engine._trades),
                initial_equity=account.initial_cash,
            ),
            coordinator=engine._engine.coordinator,
            account_view=run.account_view,
        )


async def run_streaming_paper(
    strategy: Strategy,
    source_controller: PaperSourceController,
    *,
    account: PaperAccountConfig | None = None,
    market_resolver: MarketResolver | None = None,
    data_directory: str | None = None,
    started_at: datetime | None = None,
    account_journal: object | None = None,
) -> BacktestResult:
    return await StreamingPaperRun(
        strategy,
        source_controller,
        account=account or PaperAccountConfig(),
        market_resolver=market_resolver,
        started_at=started_at,
        account_journal=account_journal,
    ).run(data_directory=data_directory)


__all__ = ["PaperAccountConfig", "PaperSourceController", "StreamingPaperRun", "run_streaming_paper"]
