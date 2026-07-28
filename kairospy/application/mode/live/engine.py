from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Mapping

from kairospy.core.account import (
    AccountContext,
    AccountState,
    AccountSnapshot,
)
from kairospy.application.context import DataContext, StrategyContext
from kairospy.core.reference import MarketResolver
from kairospy.application.runtime.model import LIVE_PROFILE, RuntimeDataEnvelope, RuntimeMode, account_data_envelope, system_data_envelope
from kairospy.application.runtime.projection.account import AccountCurrentProjection
from kairospy.application.runtime.run import RuntimeProjectionConfig, RuntimeRunner, RuntimeRunSpec, RuntimeServiceConfig, RuntimeStateConfig
from kairospy.application.runtime.source import EventSource
from kairospy.core.intent import TradeIntent
from kairospy.application.service.domains.account import (
    AccountBootstrapParser,
    AccountReconciliationService,
    LivePrivateStreamCollector,
    LivePrivateStreamPayloadAdapter,
    bootstrap_account,
)
from kairospy.application.strategy import Strategy
from kairospy.core.execution import ExecutionCoordinator
from kairospy.application.service.domains.execution import LiveExecutionAdapter, LiveTradingSafetyPolicy
from .gateway import LiveAccountGateway
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
        market_resolver: MarketResolver | None = None,
        trading_safety: LiveTradingSafetyPolicy | None = None,
    ) -> None:
        self.strategy = strategy
        self.data = data
        self.market_resolver = market_resolver or MarketResolver()
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
        self._last_account_state: AccountState | None = None
        self._account_payload_adapter = account_payload_adapter
        bind_resolver = getattr(self._account_payload_adapter, "bind_market_resolver", None)
        if callable(bind_resolver):
            bind_resolver(self.market_resolver)
        self._execution_adapter = LiveExecutionAdapter(
            account=self.account,
            coordinator=self.coordinator,
            snapshot_provider=self._require_snapshot,
            safety_policy=trading_safety,
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
        extra_events: Iterable[RuntimeDataEnvelope] = (),
        intent_deadline_monotonic: float | None = None,
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
        self._remember(bootstrap.snapshot, bootstrap.account_state)
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
        incidents = tuple(event for event in account_events if event.domain == "system")
        account_projection = AccountCurrentProjection(
            self.account,
            equity_currency=self.equity_currency,
        )
        run = RuntimeRunner.run(
            RuntimeRunSpec(
                run_id=self.account.account.account_id,
                profile=LIVE_PROFILE,
                strategy=self.strategy,
                source=_TupleEventSource(market_events),
                state_config=RuntimeStateConfig(self.data, self.market_resolver),
                service_config=RuntimeServiceConfig(
                    intent_handler=lambda intents, context, hook: self._handle_intents(
                        intents,
                        context,
                        order_params=order_params,
                        deadline_monotonic=intent_deadline_monotonic,
                    ),
                ),
                projection_config=RuntimeProjectionConfig((account_projection,)),
                started_at=started_at,
                pre_events=(bootstrap_event, *account_events, *tuple(extra_events)),
            )
        )
        self._save_state()
        return LiveRunResult(
            self.account,
            run.runtime,
            bootstrap,
            self.coordinator,
            run.views.require(account_projection.key),
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
        result = AccountReconciliationService(
            self.account,
            self.gateway,
            self.coordinator,
            self._account_payload_adapter,
            self._account_event,
        ).reconcile(
            previous=self._last_account_state,
            symbol=symbol,
            at=at,
            balance_params=balance_params,
            order_params=order_params,
        )
        self._save_state()
        return LiveReconciliationResult(result.bootstrap, result.differences, result.event)

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
        stop_drain_timeout_seconds: float = 30.0,
    ) -> LiveLoopResult:
        if max_iterations is None and stop is None and stop_token is None:
            raise ValueError("live loop requires max_iterations, stop, or stop_token")
        if max_iterations is not None and max_iterations < 1:
            raise ValueError("max_iterations must be positive")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds cannot be negative")
        if max_consecutive_failures is not None and max_consecutive_failures < 1:
            raise ValueError("max_consecutive_failures must be positive")
        if stop_drain_timeout_seconds < 0:
            raise ValueError("stop_drain_timeout_seconds cannot be negative")

        iterations: list[LiveLoopIteration] = []
        consecutive_failures = 0
        iteration_number = 0
        drained_stop = False
        while max_iterations is None or iteration_number < max_iterations:
            if stop_token is not None and stop_token.requested:
                if not drained_stop:
                    iteration_number += 1
                    iterations.append(
                        self._drain_stop(
                            iteration_number,
                            stop_token.reason,
                            symbol=symbol,
                            balance_params=balance_params,
                            order_params=order_params,
                            max_balance_events=max_balance_events,
                            max_order_events=max_order_events,
                            max_trade_events=max_trade_events,
                            monitor=monitor,
                            timeout_seconds=stop_drain_timeout_seconds,
                            consecutive_failures=consecutive_failures,
                        )
                    )
                    drained_stop = True
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
                    if not drained_stop:
                        iteration_number += 1
                        iterations.append(
                            self._drain_stop(
                                iteration_number,
                                stop_token.reason,
                                symbol=symbol,
                                balance_params=balance_params,
                                order_params=order_params,
                                max_balance_events=max_balance_events,
                                max_order_events=max_order_events,
                                max_trade_events=max_trade_events,
                                monitor=monitor,
                                timeout_seconds=stop_drain_timeout_seconds,
                                consecutive_failures=consecutive_failures,
                            )
                        )
                        drained_stop = True
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
                if not drained_stop:
                    iteration_number += 1
                    iterations.append(
                        self._drain_stop(
                            iteration_number,
                            stop_token.reason,
                            symbol=symbol,
                            balance_params=balance_params,
                            order_params=order_params,
                            max_balance_events=max_balance_events,
                            max_order_events=max_order_events,
                            max_trade_events=max_trade_events,
                            monitor=monitor,
                            timeout_seconds=stop_drain_timeout_seconds,
                            consecutive_failures=consecutive_failures,
                        )
                    )
                    drained_stop = True
                self._heartbeat(monitor, "stopped", iteration_number, stop_reason=stop_token.reason)
                break
        return LiveLoopResult(tuple(iterations))

    def _drain_stop(
        self,
        iteration_number: int,
        reason: str,
        *,
        symbol: str | None,
        balance_params: Mapping[str, object] | None,
        order_params: Mapping[str, object] | None,
        max_balance_events: int,
        max_order_events: int,
        max_trade_events: int,
        monitor: LiveLoopMonitor | None,
        timeout_seconds: float,
        consecutive_failures: int,
    ) -> LiveLoopIteration:
        started_at = datetime.now(timezone.utc)
        self._heartbeat(
            monitor,
            "draining",
            iteration_number,
            stop_reason=reason,
            consecutive_failures=consecutive_failures,
        )
        deadline = time.monotonic() + timeout_seconds
        try:
            result = self.run(
                _TupleEventSource(()),
                symbol=symbol,
                balance_params=balance_params,
                order_params=order_params,
                max_balance_events=max_balance_events,
                max_order_events=max_order_events,
                max_trade_events=max_trade_events,
                extra_events=(self._stop_event(reason),),
                intent_deadline_monotonic=deadline,
            )
            return LiveLoopIteration(
                iteration_number,
                started_at,
                datetime.now(timezone.utc),
                result=result,
                incidents=result.incidents,
            )
        except Exception as error:
            incident = self._incident_event(
                "live.stop_drain.error",
                error,
                {
                    "iteration": iteration_number,
                    "reason": reason,
                },
                None,
            )
            self._save_state()
            return LiveLoopIteration(
                iteration_number,
                started_at,
                datetime.now(timezone.utc),
                incidents=(incident,),
                error=str(error),
            )

    def _handle_intents(
        self,
        intents: tuple[object, ...],
        context: StrategyContext,
        *,
        order_params: Mapping[str, object] | None,
        deadline_monotonic: float | None,
    ) -> tuple[RuntimeDataEnvelope, ...]:
        events: list[RuntimeDataEnvelope] = []
        for value in intents:
            if not isinstance(value, TradeIntent):
                continue
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                events.append(
                    self._incident_event(
                        "live.intent_drain.timeout",
                        TimeoutError("stop drain intent deadline elapsed"),
                        {
                            "hook": context.phase,
                            "intent_id": value.intent_id,
                            "strategy_id": value.strategy_id,
                        },
                        context.now,
                    )
                )
                break
            publish_account_event = self._execution_adapter.execute_intent(value, context, order_params=order_params)
            if publish_account_event and context.now is not None:
                events.append(self._account_event(context.now, self._require_snapshot()))
        return tuple(events)

    def _stop_event(self, reason: str) -> RuntimeDataEnvelope:
        return system_data_envelope(
            "live.stop_requested",
            sequence=self._next_account_event_sequence(),
            time=datetime.now(timezone.utc),
            payload={
                "reason": reason or "operator stop requested",
                "account": {
                    "broker": self.account.account.broker,
                    "account_id": self.account.account.account_id,
                    "segment": self.account.account.segment,
                    "environment": self.account.environment.value,
                },
            },
            stream=f"system.live.stop.{self.account.account.broker}.{self.account.account.account_id}",
        )

    def _broker_symbol(self, instrument_id: str) -> str:
        return self.market_resolver.resolve(instrument_id).source_symbol

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

    def _account_event(self, at: datetime, snapshot: AccountSnapshot) -> RuntimeDataEnvelope:
        account_state = self.coordinator.account_projection(self.account, venue_snapshot=snapshot)
        self._remember(snapshot, account_state)
        return account_data_envelope(
            self.account,
            sequence=self._next_account_event_sequence(),
            time=at,
            snapshot=snapshot,
            account_state=account_state,
            pending_orders=self.coordinator.orders.active_for_context(self.account),
            source=account_state.source,
            metadata={"mode": RuntimeMode.LIVE.value},
            stream=f"account.{self.account.environment.value}.{self.account.account.broker}.{self.account.account.account_id}",
        )

    def _remember(self, snapshot: AccountSnapshot, account_state: AccountState) -> None:
        self._last_snapshot = snapshot
        self._last_account_state = account_state

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
    ) -> RuntimeDataEnvelope:
        return system_data_envelope(
            name,
            sequence=self._next_account_event_sequence(),
            time=at or datetime.now(timezone.utc),
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


def _first_time(events: tuple[RuntimeDataEnvelope, ...]) -> datetime:
    if events:
        return events[0].time
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class _TupleEventSource:
    values: tuple[RuntimeDataEnvelope, ...]

    def events(self) -> Iterable[RuntimeDataEnvelope]:
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
