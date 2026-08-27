from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from kairospy.investment.apps.account.application import AccountApplication
from kairospy.infrastructure.contracts.account.events import AccountEvent
from kairospy.strategy.apps.agent.application import AgentEvent
from kairospy.investment.apps.execution.application import (
    ExecutionApplication,
)
from kairospy.infrastructure.contracts.execution.events import ExecutionEvent
from kairospy.investment.apps.market.application import MarketApplication
from kairospy.infrastructure.contracts.market.events import MarketEvent
from kairospy.infrastructure.contracts.risk.events import RiskEvent
from kairospy.investment.apps.risk.application import RiskApplication
from kairospy.strategy import ClockAdvancedEvent, SystemEvent, TimerFiredEvent


@dataclass(frozen=True, slots=True)
class StrategyDispatch:
    domain: str
    hook: str
    event: object


@dataclass(frozen=True, slots=True)
class _SourceFailed:
    domain: str
    error: BaseException


@dataclass(frozen=True, slots=True)
class _SourceFinished:
    domain: str


class StrategySourceError(RuntimeError):
    """A module-owned event iterator failed while Strategy was consuming it."""

    def __init__(self, domain: str, cause: BaseException) -> None:
        self.domain = domain
        self.cause = cause
        super().__init__(f"{domain} event source failed: {cause}")


class StrategyEventIngress:
    """Merge module-owned typed event iterators for one Strategy instance."""

    def __init__(
        self,
        *,
        market: MarketApplication,
        account: AccountApplication,
        risk: RiskApplication,
        execution: ExecutionApplication,
        agent_events: Callable[[], AsyncIterator[object]] | None = None,
        queue_size: int = 256,
    ) -> None:
        if queue_size <= 0:
            raise ValueError("Strategy ingress queue size must be positive")
        account_events = getattr(account, "_events", None)
        if account_events is None:
            # Runtime tests use lightweight structural event sources. The
            # production AccountApplication keeps event consumption internal
            # so user-authored strategies enter through on_account callbacks.
            account_events = getattr(account, "events")
        sources: list[tuple[str, Callable[[], AsyncIterator[object]]]] = [
            ("market", market.events),
            ("account", account_events),
            ("risk", risk.events),
            ("execution", execution.events),
        ]
        if agent_events is not None:
            sources.append(("agent", agent_events))
        self._sources = tuple(sources)
        self._queue_size = queue_size

    async def events(
        self, *, include_market: bool = True
    ) -> AsyncIterator[StrategyDispatch]:
        queue: asyncio.Queue[object] = asyncio.Queue(self._queue_size)
        tasks = [
            asyncio.create_task(self._pump(domain, events, queue))
            for domain, events in self._sources
            if include_market or domain != "market"
        ]
        active = len(tasks)
        try:
            while active:
                item = await queue.get()
                if isinstance(item, _SourceFinished):
                    active -= 1
                    continue
                if isinstance(item, _SourceFailed):
                    raise StrategySourceError(item.domain, item.error) from item.error
                yield self.route(item)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def route(event: object) -> StrategyDispatch:
        if isinstance(event, AgentEvent):
            return StrategyDispatch("agent", "on_agent", event)
        if isinstance(event, MarketEvent):
            return StrategyDispatch("market", "on_market", event)
        if isinstance(event, AccountEvent):
            return StrategyDispatch("account", "on_account", event)
        if isinstance(event, RiskEvent):
            return StrategyDispatch("risk", "on_risk", event)
        if isinstance(event, ExecutionEvent):
            return StrategyDispatch("execution", "on_execution", event)
        if isinstance(event, (TimerFiredEvent, ClockAdvancedEvent)):
            return StrategyDispatch("clock", "on_clock", event)
        if isinstance(event, SystemEvent):
            return StrategyDispatch("system", "on_system", event)
        raise TypeError(f"unsupported Strategy event: {type(event).__name__}")

    @staticmethod
    async def _pump(
        domain: str,
        events: Callable[[], AsyncIterator[object]],
        queue: asyncio.Queue[object],
    ) -> None:
        try:
            async for event in events():
                await queue.put(event)
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            await queue.put(_SourceFailed(domain, error))
            return
        await queue.put(_SourceFinished(domain))
