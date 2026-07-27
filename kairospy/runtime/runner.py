from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import AsyncIterator, Iterable

from kairospy.core.account import AccountContext, AccountCurrentProjection, AccountCurrentView
from kairospy.context import DataContext
from kairospy.core.reference import MarketResolver
from kairospy.strategy import Strategy
from kairospy.core.views import ViewStore

from .components import RuntimeComponent
from .data import RuntimeDataEnvelope
from .line import RuntimeMode
from .loop import IntentHandler, StrategyRunResult, StrategyRunSession, StrategyRuntime, SubscriptionHandler
from .modes import mode_runtime_line
from .sources import AsyncEventSource, EventSource, close_async_iterator


@dataclass(frozen=True, slots=True)
class ModeRunResult:
    runtime: StrategyRunResult
    views: ViewStore
    account_view: AccountCurrentView
    account_projection: AccountCurrentProjection


@dataclass(frozen=True, slots=True)
class ModeRunSession:
    runtime: StrategyRuntime
    session: StrategyRunSession
    account_projection: AccountCurrentProjection
    mode: RuntimeMode
    pre_events: tuple[RuntimeDataEnvelope, ...] = ()
    started_at: object = None

    @property
    def views(self) -> ViewStore:
        return self.runtime.views

    async def run_async(self, source: AsyncEventSource) -> ModeRunResult:
        runtime = await self.session.run_async(
            _AsyncRuntimeModeEventSource(self.mode, source, pre_events=self.pre_events, started_at=self.started_at)
        )
        return ModeRunResult(
            runtime,
            self.runtime.views,
            self.runtime.views.require(self.account_projection.key),
            self.account_projection,
        )


class ModeRunner:
    def __init__(
        self,
        strategy: Strategy,
        data: DataContext,
        account: AccountContext,
        mode: RuntimeMode,
        *,
        equity_currency: str | None = None,
        initial_equity: Decimal | str | int | float | None = None,
        components: tuple[RuntimeComponent, ...] = (),
        market_resolver: MarketResolver | None = None,
    ) -> None:
        self.strategy = strategy
        self.data = data
        self.account = account
        self.mode = mode
        self.equity_currency = equity_currency
        self.initial_equity = initial_equity
        self.components = tuple(components)
        self.market_resolver = market_resolver or MarketResolver()

    def run(
        self,
        source: EventSource,
        *,
        pre_events: Iterable[RuntimeDataEnvelope] = (),
        started_at=None,
        intent_handler: IntentHandler | None = None,
        subscription_handler: SubscriptionHandler | None = None,
    ) -> ModeRunResult:
        account_projection = AccountCurrentProjection(
            self.account,
            equity_currency=self.equity_currency,
            initial_equity=self.initial_equity,
        )
        strategy_runtime = StrategyRuntime(
            self.strategy,
            self.data,
            components=(account_projection, *self.components),
            market_resolver=self.market_resolver,
        )
        runtime = strategy_runtime.run(
            mode_runtime_line(
                self.mode,
                (*tuple(pre_events), *tuple(source.events())),
                started_at=started_at,
            ),
            intent_handler=intent_handler,
            subscription_handler=subscription_handler,
        )
        return ModeRunResult(
            runtime,
            strategy_runtime.views,
            strategy_runtime.views.require(account_projection.key),
            account_projection,
        )

    async def run_async(
        self,
        source: AsyncEventSource,
        *,
        pre_events: Iterable[RuntimeDataEnvelope] = (),
        started_at=None,
        intent_handler: IntentHandler | None = None,
        subscription_handler: SubscriptionHandler | None = None,
    ) -> ModeRunResult:
        account_projection = AccountCurrentProjection(
            self.account,
            equity_currency=self.equity_currency,
            initial_equity=self.initial_equity,
        )
        strategy_runtime = StrategyRuntime(
            self.strategy,
            self.data,
            components=(account_projection, *self.components),
            market_resolver=self.market_resolver,
        )
        runtime = await strategy_runtime.run_async(
            _AsyncRuntimeModeEventSource(self.mode, source, pre_events=tuple(pre_events), started_at=started_at),
            intent_handler=intent_handler,
            subscription_handler=subscription_handler,
        )
        return ModeRunResult(
            runtime,
            strategy_runtime.views,
            strategy_runtime.views.require(account_projection.key),
            account_projection,
        )

    def start_async(
        self,
        *,
        pre_events: Iterable[RuntimeDataEnvelope] = (),
        started_at=None,
        intent_handler: IntentHandler | None = None,
        subscription_handler: SubscriptionHandler | None = None,
    ) -> ModeRunSession:
        account_projection = AccountCurrentProjection(
            self.account,
            equity_currency=self.equity_currency,
            initial_equity=self.initial_equity,
        )
        strategy_runtime = StrategyRuntime(
            self.strategy,
            self.data,
            components=(account_projection, *self.components),
            market_resolver=self.market_resolver,
        )
        session = strategy_runtime.start(intent_handler=intent_handler, subscription_handler=subscription_handler)
        return ModeRunSession(
            strategy_runtime,
            session,
            account_projection,
            self.mode,
            pre_events=tuple(pre_events),
            started_at=started_at,
        )


__all__ = ["ModeRunResult", "ModeRunSession", "ModeRunner"]


class _AsyncRuntimeModeEventSource:
    def __init__(
        self,
        mode: RuntimeMode,
        source: AsyncEventSource,
        *,
        pre_events: tuple[RuntimeDataEnvelope, ...],
        started_at,
    ) -> None:
        self.mode = mode
        self.source = source
        self.pre_events = pre_events
        self.started_at = started_at

    async def events(self) -> AsyncIterator[RuntimeDataEnvelope]:
        start_time = self.started_at
        if start_time is not None:
            from .data import system_data_envelope

            yield system_data_envelope(
                f"runtime.mode.{self.mode.value}.started",
                sequence=1,
                time=start_time,
                payload={"mode": self.mode.value},
                stream="system.runtime",
            )
        for event in self.pre_events:
            yield event
        events = self.source.events()
        try:
            async for event in events:
                yield event
        finally:
            await close_async_iterator(events)
