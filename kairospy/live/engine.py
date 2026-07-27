from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Mapping

from kairospy.accounts import (
    AccountContext,
    AccountProjection,
    AccountSnapshot,
    AccountBootstrapParser,
    bootstrap_account,
    compare_account_state,
)
from kairospy.context import DataContext
from kairospy.runtime import (
    AccountRuntimeEvent,
    EventSource,
    ModeRunner,
    RuntimeEvent,
    RuntimeMode,
    SystemRuntimeEvent,
)
from kairospy.intents import TradeIntent
from kairospy.strategy import Strategy, StrategyContext
from kairospy.execution import ExecutionCoordinator, LiveExecutionAdapter
from .gateway import LiveAccountGateway
from .private_stream import LivePrivateStreamCollector, LivePrivateStreamPayloadAdapter
from .result import (
    LiveLoopHeartbeat,
    LiveLoopIteration,
    LiveLoopMonitor,
    LiveLoopResult,
    LiveReconciliationResult,
    LiveRunResult,
    LiveStopToken,
)
from .state import LiveRuntimeStateStore


LiveLoopStop = Callable[[LiveLoopIteration], bool]
LiveSourceFactory = Callable[[int], EventSource]


class LiveEngine:
    def __init__(
        self,
        strategy: Strategy,
        data: DataContext,
        account: AccountContext,
        gateway: LiveAccountGateway,
        *,
        account_payload_adapter: AccountBootstrapParser | LivePrivateStreamPayloadAdapter,
        coordinator: ExecutionCoordinator | None = None,
        equity_currency: str | None = None,
        state_store: LiveRuntimeStateStore | None = None,
    ) -> None:
        self.strategy = strategy
        self.data = data
        self.account = account
        self.gateway = gateway
        self.coordinator = coordinator or ExecutionCoordinator(
            broker=gateway,
            broker_symbol_resolver=self._broker_symbol,
        )
        if coordinator is not None:
            self.coordinator.broker = self.coordinator.broker or gateway
            self.coordinator.broker_symbol_resolver = self._broker_symbol
        self.equity_currency = equity_currency
        self.state_store = state_store
        self._account_event_sequence = 0
        self._last_snapshot: AccountSnapshot | None = None
        self._last_projection: AccountProjection | None = None
        self._account_payload_adapter = account_payload_adapter
        bind_resolver = getattr(self._account_payload_adapter, "bind_market_resolver", None)
        if callable(bind_resolver):
            bind_resolver(self.data.markets)
        self._execution_adapter = LiveExecutionAdapter(
            account=self.account,
            coordinator=self.coordinator,
            snapshot_provider=self._require_snapshot,
        )
        self._private_stream = LivePrivateStreamCollector(
            self.gateway,
            self.account,
            self.coordinator,
            self._account_payload_adapter,
            self._account_event,
            self._incident_event,
        )
        self._restore_state()

    def run(
        self,
        source: EventSource,
        *,
        symbol: str | None = None,
        balance_params: Mapping[str, object] | None = None,
        order_params: Mapping[str, object] | None = None,
        max_balance_events: int = 0,
        max_order_events: int = 0,
        max_trade_events: int = 0,
    ) -> LiveRunResult:
        market_events = tuple(source.events())
        started_at = _first_time(market_events)
        bootstrap = bootstrap_account(
            self.account,
            self.gateway,
            self.coordinator,
            self._account_payload_adapter,
            symbol=symbol,
            at=started_at,
            balance_params=balance_params,
            order_params=order_params,
        )
        self._remember(bootstrap.snapshot, bootstrap.projection)
        bootstrap_event = self._account_event(started_at, bootstrap.snapshot)
        account_events = asyncio.run(
            self._private_stream.collect(
                bootstrap.snapshot,
                symbol=symbol,
                balance_params=balance_params,
                order_params=order_params,
                max_balance_events=max_balance_events,
                max_order_events=max_order_events,
                max_trade_events=max_trade_events,
            )
        )
        incidents = tuple(event for event in account_events if isinstance(event, SystemRuntimeEvent))
        runner = ModeRunner(
            self.strategy,
            self.data,
            self.account,
            RuntimeMode.LIVE,
            equity_currency=self.equity_currency,
        )
        run = runner.run(
            _TupleEventSource(market_events),
            pre_events=(bootstrap_event, *account_events),
            started_at=started_at,
            intent_handler=lambda intents, context, hook: self._handle_intents(
                intents,
                context,
                order_params=order_params,
            ),
        )
        self._save_state()
        return LiveRunResult(
            self.account,
            run.runtime,
            bootstrap,
            self.coordinator,
            run.account_view,
            incidents,
        )

    def reconcile_account(
        self,
        *,
        symbol: str | None = None,
        at: datetime | None = None,
        balance_params: Mapping[str, object] | None = None,
        order_params: Mapping[str, object] | None = None,
    ) -> LiveReconciliationResult:
        observed_at = at or datetime.now(timezone.utc)
        if observed_at.tzinfo is None:
            raise ValueError("reconciliation timestamp must be timezone-aware")
        previous = self._last_projection
        bootstrap = bootstrap_account(
            self.account,
            self.gateway,
            self.coordinator,
            self._account_payload_adapter,
            symbol=symbol,
            at=observed_at,
            balance_params=balance_params,
            order_params=order_params,
        )
        differences = () if previous is None else compare_account_state(previous, bootstrap.snapshot)
        self._remember(bootstrap.snapshot, bootstrap.projection)
        result = LiveReconciliationResult(
            bootstrap,
            differences,
            self._account_event(observed_at, bootstrap.snapshot),
        )
        self._save_state()
        return result

    def run_loop(
        self,
        source_factory: LiveSourceFactory,
        *,
        symbol: str | None = None,
        balance_params: Mapping[str, object] | None = None,
        order_params: Mapping[str, object] | None = None,
        max_balance_events: int = 0,
        max_order_events: int = 0,
        max_trade_events: int = 0,
        max_iterations: int | None = None,
        stop: LiveLoopStop | None = None,
        stop_token: LiveStopToken | None = None,
        monitor: LiveLoopMonitor | None = None,
        retry_backoff_seconds: float = 1.0,
        max_consecutive_failures: int | None = None,
    ) -> LiveLoopResult:
        if max_iterations is None and stop is None and stop_token is None:
            raise ValueError("live loop requires max_iterations, stop, or stop_token")
        if max_iterations is not None and max_iterations < 1:
            raise ValueError("max_iterations must be positive")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds cannot be negative")
        if max_consecutive_failures is not None and max_consecutive_failures < 1:
            raise ValueError("max_consecutive_failures must be positive")

        iterations: list[LiveLoopIteration] = []
        consecutive_failures = 0
        iteration_number = 0
        while max_iterations is None or iteration_number < max_iterations:
            if stop_token is not None and stop_token.requested:
                self._heartbeat(
                    monitor,
                    "stopped",
                    iteration_number,
                    stop_reason=stop_token.reason,
                    consecutive_failures=consecutive_failures,
                )
                break
            iteration_number += 1
            started_at = datetime.now(timezone.utc)
            self._heartbeat(monitor, "starting", iteration_number, consecutive_failures=consecutive_failures)
            try:
                result = self.run(
                    source_factory(iteration_number),
                    symbol=symbol,
                    balance_params=balance_params,
                    order_params=order_params,
                    max_balance_events=max_balance_events,
                    max_order_events=max_order_events,
                    max_trade_events=max_trade_events,
                )
                consecutive_failures = 0
                iteration = LiveLoopIteration(
                    iteration_number,
                    started_at,
                    datetime.now(timezone.utc),
                    result=result,
                    incidents=result.incidents,
                )
                self._heartbeat(monitor, "succeeded", iteration_number)
            except Exception as error:
                consecutive_failures += 1
                incident = self._incident_event(
                    "live.loop.error",
                    error,
                    {
                        "iteration": iteration_number,
                        "consecutive_failures": consecutive_failures,
                    },
                    None,
                )
                self._save_state()
                iteration = LiveLoopIteration(
                    iteration_number,
                    started_at,
                    datetime.now(timezone.utc),
                    incidents=(incident,),
                    error=str(error),
                )
                self._heartbeat(
                    monitor,
                    "failed",
                    iteration_number,
                    error=str(error),
                    consecutive_failures=consecutive_failures,
                )
                iterations.append(iteration)
                if stop is not None and stop(iteration):
                    self._heartbeat(monitor, "stopped", iteration_number, stop_reason="stop predicate")
                    break
                if stop_token is not None and stop_token.requested:
                    self._heartbeat(monitor, "stopped", iteration_number, stop_reason=stop_token.reason)
                    break
                if max_consecutive_failures is not None and consecutive_failures >= max_consecutive_failures:
                    self._heartbeat(
                        monitor,
                        "stopped",
                        iteration_number,
                        stop_reason="max consecutive failures",
                        consecutive_failures=consecutive_failures,
                    )
                    break
                if retry_backoff_seconds:
                    time.sleep(retry_backoff_seconds)
                continue

            iterations.append(iteration)
            if stop is not None and stop(iteration):
                self._heartbeat(monitor, "stopped", iteration_number, stop_reason="stop predicate")
                break
            if stop_token is not None and stop_token.requested:
                self._heartbeat(monitor, "stopped", iteration_number, stop_reason=stop_token.reason)
                break
        return LiveLoopResult(tuple(iterations))

    def _handle_intents(
        self,
        intents: tuple[object, ...],
        context: StrategyContext,
        *,
        order_params: Mapping[str, object] | None,
    ) -> tuple[AccountRuntimeEvent, ...]:
        events: list[AccountRuntimeEvent] = []
        for value in intents:
            if not isinstance(value, TradeIntent):
                continue
            publish_account_event = self._execution_adapter.execute_intent(value, context, order_params=order_params)
            if publish_account_event and context.now is not None:
                events.append(self._account_event(context.now, self._require_snapshot()))
        return tuple(events)

    def _broker_symbol(self, instrument_id: str) -> str:
        return self.data.markets.resolve(instrument_id).source_symbol

    def _restore_state(self) -> None:
        if self.state_store is None:
            return
        snapshot = self.state_store.load()
        if snapshot is None:
            return
        snapshot.restore_into(self.coordinator, self._private_stream.state)

    def _save_state(self) -> None:
        if self.state_store is None:
            return
        self.state_store.save(self.coordinator, self._private_stream.state)

    def _heartbeat(
        self,
        monitor: LiveLoopMonitor | None,
        status,
        iteration: int,
        *,
        error: str = "",
        stop_reason: str = "",
        consecutive_failures: int = 0,
    ) -> None:
        if monitor is None:
            return
        monitor.heartbeat(
            LiveLoopHeartbeat(
                status,
                iteration,
                datetime.now(timezone.utc),
                self.account,
                error=error,
                stop_reason=stop_reason,
                consecutive_failures=consecutive_failures,
            )
        )

    def _account_event(self, at: datetime, snapshot: AccountSnapshot) -> AccountRuntimeEvent:
        projection = self.coordinator.account_projection(self.account, venue_snapshot=snapshot)
        self._remember(snapshot, projection)
        return AccountRuntimeEvent(
            self.account,
            self._next_account_event_sequence(),
            at,
            payload={"mode": RuntimeMode.LIVE.value, "source": projection.source.value},
            snapshot=snapshot,
            projection=projection,
            stream=f"account.{self.account.environment.value}.{self.account.account.broker}.{self.account.account.account_id}",
        )

    def _remember(self, snapshot: AccountSnapshot, projection: AccountProjection) -> None:
        self._last_snapshot = snapshot
        self._last_projection = projection

    def _require_snapshot(self) -> AccountSnapshot:
        if self._last_snapshot is None:
            raise RuntimeError("live engine has no account snapshot")
        return self._last_snapshot

    def _next_account_event_sequence(self) -> int:
        self._account_event_sequence += 1
        return self._account_event_sequence

    def _incident_event(
        self,
        name: str,
        error: Exception,
        raw: Mapping[str, object],
        at: datetime | None = None,
    ) -> SystemRuntimeEvent:
        return SystemRuntimeEvent(
            name,
            self._next_account_event_sequence(),
            at or datetime.now(timezone.utc),
            payload={
                "account": {
                    "broker": self.account.account.broker,
                    "account_id": self.account.account.account_id,
                    "segment": self.account.account.segment,
                    "environment": self.account.environment.value,
                },
                "error_type": type(error).__name__,
                "error": str(error),
                "raw": dict(raw),
            },
            stream=f"system.live.account.{self.account.account.broker}.{self.account.account.account_id}",
        )


def _first_time(events: tuple[RuntimeEvent, ...]) -> datetime:
    if events:
        return events[0].time
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class _TupleEventSource:
    values: tuple[RuntimeEvent, ...]

    def events(self) -> Iterable[RuntimeEvent]:
        return iter(self.values)


__all__ = [
    "LiveAccountGateway",
    "LiveEngine",
    "LiveLoopResult",
    "LiveLoopIteration",
    "LiveLoopHeartbeat",
    "LiveLoopMonitor",
    "LiveStopToken",
    "LiveReconciliationResult",
    "LiveRunResult",
]
