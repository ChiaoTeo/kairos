from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Protocol

from kairospy.strategy import ClockAdvancedEvent, SystemEvent, TimerFiredEvent
from kairospy.strategy.apps.agent.application import AgentEvent


@dataclass(frozen=True, slots=True)
class StrategyDispatch:
    domain: str
    hook: str
    event: object


@dataclass(frozen=True, slots=True)
class PollSummary:
    fragment_count: int
    dispatch_count: int
    source_fragments: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class _SourceFailed:
    domain: str
    error: BaseException


class StrategySourceError(RuntimeError):
    """An owner event source failed while Strategy was polling it."""

    def __init__(self, domain: str, cause: BaseException) -> None:
        self.domain = domain
        self.cause = cause
        super().__init__(f"{domain} event source failed: {cause}")


class _LiveApplication(Protocol):
    def visit_live(
        self,
        visitor: Callable[[object], None],
        *,
        fragment_limit: int = 64,
    ) -> int: ...

    def close_live(self) -> None: ...


class StrategyEventIngress:
    """Round-robin direct poll of callback-scoped business events.

    Aeron business facts are never put in an asyncio queue. Agent events are
    owned Strategy facts and retain a separate bounded queue.
    """

    def __init__(
        self,
        *,
        market: _LiveApplication,
        account: _LiveApplication,
        risk: _LiveApplication,
        execution: _LiveApplication,
        agent_events: Callable[[], AsyncIterator[object]] | None = None,
        queue_size: int = 256,
    ) -> None:
        if queue_size <= 0:
            raise ValueError("Strategy ingress queue size must be positive")
        self._sources = (
            ("market", "on_market", market.visit_live),
            ("account", "on_account", account.visit_live),
            ("risk", "on_risk", risk.visit_live),
            ("execution", "on_execution", execution.visit_live),
        )
        self._applications = (market, account, risk, execution)
        self._next_source = 0
        self._agent_events = agent_events
        self._owned_queue: asyncio.Queue[object] = asyncio.Queue(queue_size)
        self._agent_task: asyncio.Task[None] | None = None

    def poll_once(
        self,
        dispatch: Callable[[StrategyDispatch], None],
        *,
        include_market: bool = True,
        fragment_limit: int = 64,
    ) -> PollSummary:
        if fragment_limit <= 0:
            raise ValueError("fragment_limit must be positive")
        fragments = 0
        dispatched = 0
        per_source: list[tuple[str, int]] = []
        source_count = len(self._sources)
        for offset in range(source_count):
            index = (self._next_source + offset) % source_count
            domain, hook, visit = self._sources[index]
            if domain == "market" and not include_market:
                continue

            def accept(event: object, *, domain: str = domain, hook: str = hook) -> None:
                nonlocal dispatched
                dispatch(StrategyDispatch(domain, hook, event))
                dispatched += 1

            try:
                count = visit(accept, fragment_limit=fragment_limit)
            except BaseException as error:
                raise StrategySourceError(domain, error) from error
            fragments += count
            per_source.append((domain, count))
        self._next_source = (self._next_source + 1) % source_count
        return PollSummary(fragments, dispatched, tuple(per_source))

    @staticmethod
    def route(event: object) -> StrategyDispatch:
        """Route owned/non-polled events; live poll already supplies its domain."""

        if isinstance(event, AgentEvent):
            return StrategyDispatch("agent", "on_agent", event)
        if isinstance(event, (TimerFiredEvent, ClockAdvancedEvent)):
            return StrategyDispatch("clock", "on_clock", event)
        if isinstance(event, SystemEvent):
            return StrategyDispatch("system", "on_system", event)
        metadata = getattr(event, "metadata", None)
        stream_id = getattr(metadata, "stream_id", "")
        if stream_id == "market.events":
            return StrategyDispatch("market", "on_market", event)
        if stream_id.startswith("account.events/"):
            return StrategyDispatch("account", "on_account", event)
        if stream_id == "risk.events":
            return StrategyDispatch("risk", "on_risk", event)
        if stream_id == "execution.events":
            return StrategyDispatch("execution", "on_execution", event)
        raise TypeError(f"unsupported Strategy event: {type(event).__name__}")

    def start_owned(self) -> None:
        if self._agent_events is None or self._agent_task is not None:
            return
        self._agent_task = asyncio.create_task(self._pump_agent())

    def drain_owned(self, dispatch: Callable[[StrategyDispatch], None]) -> int:
        count = 0
        while True:
            try:
                item = self._owned_queue.get_nowait()
            except asyncio.QueueEmpty:
                return count
            if isinstance(item, _SourceFailed):
                raise StrategySourceError(item.domain, item.error) from item.error
            dispatch(StrategyDispatch("agent", "on_agent", item))
            count += 1

    async def close(self) -> None:
        if self._agent_task is not None:
            task, self._agent_task = self._agent_task, None
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for application in self._applications:
            application.close_live()

    async def _pump_agent(self) -> None:
        assert self._agent_events is not None
        try:
            async for event in self._agent_events():
                await self._owned_queue.put(event)
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            await self._owned_queue.put(_SourceFailed("agent", error))


__all__ = [
    "PollSummary",
    "StrategyDispatch",
    "StrategyEventIngress",
    "StrategySourceError",
]
