from __future__ import annotations

from dataclasses import dataclass, replace
import asyncio
from collections import deque
import inspect
from datetime import datetime
from typing import Mapping

from ..domain.lifecycle import StrategyDataHealth, StrategyLifecycle, StrategyReadiness
from ..domain.messages import LifecycleRecord, RawEventEnvelope
from ..protocol import LifecycleJournal, Strategy
from .context import StrategyClientBundle, StrategyContext
from .applications import compose_strategy_applications
from kairospy.strategy import (
    AccountSnapshotEvent,
    BalanceEvent,
    ClockAdvance,
    ClockAdvancedEvent,
    EventMetadata,
    FillEvent,
    IntentUpdateEvent,
    MarketEvent,
    RiskStatusEvent,
    StrategyLogger,
    SystemEvent,
    SystemNotice,
    TimerFiredEvent,
    OrderUpdateEvent,
    PositionEvent,
)
from kairospy.strategy import CommandResult, StrategyCommand
from kairospy.strategy.clock import (
    DeterministicTimerQueue,
    StrategyClock,
    TimerEvent,
    ensure_utc,
)
from kairospy.domain_types import AccountId
from kairospy.application.execution.mapping import (
    map_execution_fill,
    map_execution_intent,
    map_execution_order,
)
from kairospy.application.market.mapping import map_market_event
from kairospy.application.account.mapping import (
    map_account_snapshot,
    map_balance,
    map_position,
)
from kairospy.application.risk.mapping import map_risk_status


COMMAND_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class StrategyHostStatus:
    launch_id: str
    instance_id: str
    strategy_id: str
    state: StrategyLifecycle
    reason: str | None = None
    event_sequence: int = 0
    readiness: StrategyReadiness = StrategyReadiness.NOT_STARTED
    data_health: StrategyDataHealth = StrategyDataHealth.NOT_STARTED
    subscription_count: int = 0
    active_subscription_count: int = 0
    first_event_received: bool = False
    last_event_time: datetime | None = None
    last_event_kind: str | None = None
    event_count: int = 0
    subscriptions: tuple[Mapping[str, object], ...] = ()


class StrategyHost:
    """Instance-owned adapter around one user Strategy.

    Launch owns this host's lifecycle. The host owns the strategy callback loop,
    but never owns Market, Account, Risk, or Execution state.
    """

    def __init__(
        self,
        strategy: Strategy,
        *,
        launch_id: str,
        instance_id: str,
        clients: StrategyClientBundle,
        journal: LifecycleJournal,
        params: Mapping[str, object] | None = None,
        logger: StrategyLogger | None = None,
        snapshot_views: tuple[str, ...] = ("market.current",),
        replay_end: datetime | None = None,
    ) -> None:
        if not launch_id.strip() or not instance_id.strip():
            raise ValueError("launch_id and instance_id are required")
        self.strategy = strategy
        self.launch_id = launch_id
        self.instance_id = instance_id
        self.clients = clients
        self._snapshots = clients.market_snapshots
        self.journal = journal
        self.logger = logger or StrategyLogger(
            fields={
                "launch_id": launch_id,
                "instance_id": instance_id,
                "strategy_id": strategy.strategy_id,
                "component": "strategy",
            }
        )
        applications = compose_strategy_applications(
            strategy_id=strategy.strategy_id,
            instance_id=instance_id,
            market_commands=clients.market_commands,
            execution_commands=clients.execution_commands,
            market_snapshots=clients.market_snapshots,
            reference_client=clients.reference_client,
            account_projections=clients.account_projections,
            execution_projection=clients.execution_projection,
            risk_projection=clients.risk_projection,
            subscription_observer=self._observe_subscription,
        )
        self.context = StrategyContext(
            strategy.strategy_id,
            applications=applications,
            launch_id=launch_id,
            instance_id=instance_id,
            params=params,
            state_path=clients.state_path,
            logger=self.logger,
        )
        self.snapshot_views = snapshot_views
        self._status = StrategyHostStatus(
            launch_id, instance_id, strategy.strategy_id, StrategyLifecycle.CREATED
        )
        self._subscription_requests: set[str] = set()
        self._subscriptions: dict[str, dict[str, object]] = {}
        self._subscription_owner_released = False
        self._stop_requested = asyncio.Event()
        self._command_active = False
        self._queued_events: deque[RawEventEnvelope] = deque(maxlen=256)
        self.equity_curve: list[dict[str, object]] = []
        self._timers = DeterministicTimerQueue()
        self._timer_sequence = 0
        self._clock = StrategyClock(self._timers.schedule, self._timers.cancel)
        self.context.clock = self._clock
        self._replay_end = replay_end
        self._pending_bar_event: RawEventEnvelope | None = None
        self._last_data_event: RawEventEnvelope | None = None
        self.clock_events: list[dict[str, object]] = []
        self.event_trace: list[dict[str, object]] = []
        self._trace_sequence = 0
        self._stream_sequences: dict[str, int] = {}
        self.backtest_fills: list[Mapping[str, object]] = []
        self._log("strategy host created", event="strategy_host_created")

    @property
    def status(self) -> StrategyHostStatus:
        return self._status

    @property
    def stream(self):
        """Compatibility view; ownership remains in ``clients``."""
        return self.clients.market_events

    def start(self) -> StrategyHostStatus:
        if self._status.state is not StrategyLifecycle.CREATED:
            raise RuntimeError(
                f"strategy can only start from created: {self._status.state}"
            )
        self._transition(StrategyLifecycle.WAITING_FOR_DEPENDENCIES)
        self._log("strategy startup begin", event="strategy_starting")
        self._log("strategy on_start begin", event="strategy_on_start_begin")
        try:
            self._call("on_start", self.context._bind(None))
        except Exception as error:
            self._release_subscriptions_best_effort()
            self._transition(StrategyLifecycle.FAILED, str(error))
            raise
        self._log(
            f"strategy on_start completed subscriptions={len(self._subscription_requests)}",
            event="strategy_on_start_completed",
            subscription_count=len(self._subscription_requests),
        )
        try:
            if not self._refresh_dependencies():
                self._log(
                    f"waiting for dependencies reason={self._status.reason}",
                    event="dependencies_waiting",
                )
                return self._status
            if not self._bootstrap():
                self._log(
                    f"waiting for snapshot reason={self._status.reason}",
                    event="snapshot_waiting",
                )
                return self._status
        except Exception as error:
            self._release_subscriptions_best_effort()
            self._transition(StrategyLifecycle.FAILED, str(error))
            raise
        self._status = replace(self._status, readiness=StrategyReadiness.READY)
        self._transition(StrategyLifecycle.READY)
        self._log("strategy startup ready", event="strategy_ready")
        return self._status

    def enable(self) -> StrategyHostStatus:
        if self._status.state is not StrategyLifecycle.READY:
            raise RuntimeError(
                f"strategy can only be enabled from ready: {self._status.state}"
            )
        self._transition(StrategyLifecycle.RUNNING)
        self._status = replace(
            self._status, data_health=StrategyDataHealth.WAITING_FOR_DATA
        )
        self._log("strategy enabled; waiting for market data", event="strategy_running")
        return self._status

    def pause(self, reason: str = "paused by control") -> StrategyHostStatus:
        if self._status.state is not StrategyLifecycle.RUNNING:
            raise RuntimeError(
                f"strategy can only be paused from running: {self._status.state}"
            )
        self._transition(StrategyLifecycle.PAUSED, reason)
        return self._status

    def resume(self) -> StrategyHostStatus:
        if self._status.state is not StrategyLifecycle.PAUSED:
            raise RuntimeError(
                f"strategy can only resume from paused: {self._status.state}"
            )
        self._transition(StrategyLifecycle.RUNNING)
        self._status = replace(
            self._status, data_health=StrategyDataHealth.WAITING_FOR_DATA
        )
        self._log("strategy resumed; waiting for market data", event="strategy_resumed")
        return self._status

    def refresh(self) -> StrategyHostStatus:
        if self._status.state is not StrategyLifecycle.WAITING_FOR_DEPENDENCIES:
            return self._status
        try:
            if not self._refresh_dependencies():
                return self._status
            if not self._bootstrap():
                return self._status
        except Exception as error:
            self._release_subscriptions_best_effort()
            self._transition(StrategyLifecycle.FAILED, str(error))
            raise
        self._status = replace(self._status, readiness=StrategyReadiness.READY)
        self._transition(StrategyLifecycle.READY)
        self._log("strategy startup ready", event="strategy_ready")
        return self._status

    def dispatch(self, event: RawEventEnvelope) -> None:
        if self._command_active:
            if len(self._queued_events) == self._queued_events.maxlen:
                raise RuntimeError("strategy command event queue overflowed")
            self._queued_events.append(event)
            return
        if event.domain in {"data", "clock"} and event.occurred_at is not None:
            self.advance_time(event.occurred_at)
        self._dispatch_event(event)

    def _dispatch_replay_event(self, event: RawEventEnvelope) -> None:
        """Merge due strategy timers before the next replay observation.

        The market stream remains the source of observations, but the virtual
        clock is allowed to visit timer timestamps inside a data gap.  Clock
        events at the same timestamp are emitted before the market event.
        """
        if event.occurred_at is None:
            self._dispatch_event(event)
            return
        event_time = ensure_utc(event.occurred_at)
        self._advance_replay_time(event_time)
        self._dispatch_event(event)

    def _advance_replay_time(self, target: datetime) -> None:
        """Drain the replay clock queue up to ``target`` in stable order."""
        target = ensure_utc(target)
        while (next_due := self._timers.next_due()) is not None and next_due <= target:
            self.advance_time(next_due)
        self.advance_time(target)

    def advance_time(self, value: datetime) -> None:
        """Advance business time and deliver due timer events.

        Replay drivers may call this without a MarketEvent, which is required
        for timers during data gaps.  Live callers should use the runtime's
        real-time clock adapter rather than wall time in strategy code.
        """
        current = ensure_utc(value)
        if self._clock.now is not None and current < self._clock.now:
            raise ValueError("strategy business time cannot move backwards")
        self._clock._set_now(current)
        if self.clients.backtest_time_advance is not None:
            event_time_unix_nanos = int(current.timestamp() * 1_000_000_000)
            self.clients.backtest_time_advance(event_time_unix_nanos)
        for timer in self._timers.pop_due(current):
            self._dispatch_timer(timer)

    def _dispatch_timer(self, timer: TimerEvent) -> None:
        self._timer_sequence = (
            max(self._timer_sequence, self._status.event_sequence) + 1
        )
        event = RawEventEnvelope(
            f"strategy.clock:{self.instance_id}",
            self._timer_sequence,
            "clock",
            "timer",
            {
                "timer_id": timer.timer_id,
                "scheduled_at": timer.scheduled_at,
                "event_time": timer.event_time,
            },
            timer.event_time,
        )
        self.clock_events.append(
            {
                "timer_id": timer.timer_id,
                "scheduled_at": timer.scheduled_at,
                "event_time": timer.event_time,
                "sequence": event.sequence,
                "trace_sequence": self._trace_sequence + 1,
            }
        )
        self._dispatch_event(event)

    def _dispatch_event(self, event: RawEventEnvelope) -> None:
        if self._status.state is not StrategyLifecycle.RUNNING:
            return
        known_streams = {
            self.clients.market_events.stream_id,
            f"strategy.clock:{self.instance_id}",
            *(stream.stream_id for stream in self.clients.application_events),
        }
        if event.stream_id not in known_streams:
            raise ValueError("event belongs to a different stream")
        previous_sequence = self._stream_sequences.get(event.stream_id, 0)
        if event.sequence <= previous_sequence:
            raise ValueError(
                f"event stream {event.stream_id} did not advance: "
                f"previous={previous_sequence}, received={event.sequence}"
            )
        self._stream_sequences[event.stream_id] = event.sequence
        self._trace_sequence += 1
        self.event_trace.append(
            {
                "trace_sequence": self._trace_sequence,
                "domain": event.domain,
                "kind": event.kind,
                "event_time": event.occurred_at,
                "source_sequence": event.sequence,
            }
        )
        # The live Unix stream has no replay/acknowledgement handshake, so a
        # subscriber can legitimately miss events while attaching.  The
        # snapshot supplies the initial state; thereafter the stream advances
        # the watermark to each received event without claiming replay-grade
        # continuity.
        # A completed bar can only be used for execution on the next market
        # event.  This prevents a strategy from observing a bar close and
        # immediately filling against that same close by accident.  Quote
        # events keep the existing quote-after-intent behavior.
        if event.domain == "data" and event.kind == "bar":
            if self._pending_bar_event is not None:
                self._apply_backtest_callbacks(self._pending_bar_event)
            self._pending_bar_event = event
            self._last_data_event = event
        elif event.domain == "data":
            self._last_data_event = event

        typed_event, hook = self._typed_event(event)
        self.context._bind(typed_event)
        event_time_source = (
            "none"
            if event.occurred_at is None
            else "market_event"
            if event.domain == "data"
            else f"{event.domain}_event"
        )
        with self.logger.bind_event(
            event_time=event.occurred_at,
            event_time_source=event_time_source,
            event_sequence=event.sequence,
        ):
            if hook == "on_market":
                if getattr(self.strategy, "log_on_market", False):
                    self._log(
                        "strategy on_market event",
                        event="strategy_on_market",
                        event_domain=event.domain,
                        event_kind=event.kind,
                        event_payload=repr(event.payload),
                    )
            else:
                self._log(f"dispatch {hook}", event_kind=event.kind)
            try:
                self._call(hook, self.context, typed_event)
            except Exception as error:
                self._release_subscriptions_best_effort()
                self._transition(StrategyLifecycle.FAILED, str(error))
                raise
        if event.domain == "data" and event.kind != "bar":
            self._apply_backtest_callbacks(event)
        first_event = not self._status.first_event_received and event.domain == "data"
        self._status = replace(
            self._status,
            event_sequence=(
                self._status.event_sequence
                if event.domain == "clock"
                else max(self._status.event_sequence, event.sequence)
            ),
            data_health=(
                StrategyDataHealth.HEALTHY
                if event.domain == "data"
                else self._status.data_health
            ),
            first_event_received=(
                True if event.domain == "data" else self._status.first_event_received
            ),
            last_event_time=event.occurred_at,
            last_event_kind=event.kind,
            event_count=self._status.event_count + 1,
        )
        if first_event:
            self._log(
                "first strategy data event received",
                event="first_data_event_received",
                event_kind=event.kind,
                event_sequence=event.sequence,
            )

    def _typed_event(self, event: RawEventEnvelope):
        if event.domain in {"data", "market"}:
            return map_market_event(
                event, dispatch_sequence=self._trace_sequence
            ), "on_market"
        metadata = EventMetadata(
            stream_id=event.stream_id,
            sequence=event.sequence,
            dispatch_sequence=self._trace_sequence,
            schema_version=event.schema_version,
            producer=event.producer or event.domain,
            occurred_at=event.occurred_at,
            causation_id=event.causation_id,
        )
        if event.domain == "clock":
            payload = event.payload
            if not isinstance(payload, Mapping):
                raise ValueError("Clock event payload must be an object")
            if event.kind == "advance":
                if event.occurred_at is None:
                    raise ValueError("Clock advance event requires occurred_at")
                return ClockAdvancedEvent(
                    ClockAdvance(
                        event.occurred_at, str(payload.get("source", "runtime"))
                    ),
                    metadata,
                ), "on_clock"
            timer_id = payload.get("timer_id")
            scheduled_at = payload.get("scheduled_at")
            event_time = payload.get("event_time", event.occurred_at)
            if (
                not isinstance(timer_id, str)
                or not isinstance(scheduled_at, datetime)
                or not isinstance(event_time, datetime)
            ):
                raise ValueError("Clock timer event is missing typed fields")
            return TimerFiredEvent(
                TimerEvent(timer_id, scheduled_at, event_time), metadata
            ), "on_clock"
        if event.domain == "system":
            if isinstance(event.payload, SystemNotice):
                notice = event.payload
            elif isinstance(event.payload, Mapping):
                notice = SystemNotice(
                    code=str(event.payload.get("code", event.kind)),
                    message=str(event.payload.get("message", "")),
                )
            else:
                raise ValueError("System event payload must be a SystemNotice")
            return SystemEvent(notice, metadata), "on_system"
        if event.domain == "account":
            if isinstance(
                event.payload, (AccountSnapshotEvent, BalanceEvent, PositionEvent)
            ):
                return event.payload, "on_account"
            account_id = self._scoped_account_id(event.payload)
            if event.kind == "snapshot":
                return AccountSnapshotEvent(
                    map_account_snapshot(event.payload, account_id=account_id), metadata
                ), "on_account"
            if event.kind == "balance":
                return BalanceEvent(
                    map_balance(event.payload, account_id=account_id), metadata
                ), "on_account"
            if event.kind == "position":
                return PositionEvent(
                    map_position(event.payload, account_id=account_id), metadata
                ), "on_account"
            raise ValueError(f"unsupported Account event kind: {event.kind}")
        if event.domain == "risk":
            if isinstance(event.payload, RiskStatusEvent):
                return event.payload, "on_risk"
            account_id = self._scoped_account_id(event.payload)
            if event.kind != "status":
                raise ValueError(f"unsupported Risk event kind: {event.kind}")
            return RiskStatusEvent(
                map_risk_status(event.payload, account_id=account_id), metadata
            ), "on_risk"
        if event.domain in {"execution", "intent"}:
            if isinstance(
                event.payload, (IntentUpdateEvent, OrderUpdateEvent, FillEvent)
            ):
                return event.payload, "on_execution"
            if event.kind in {"intent", "intent_update"}:
                return IntentUpdateEvent(
                    map_execution_intent(event.payload, event_sequence=event.sequence),
                    metadata,
                ), "on_execution"
            if event.kind in {"order", "order_update"}:
                return OrderUpdateEvent(
                    map_execution_order(event.payload, event_sequence=event.sequence),
                    metadata,
                ), "on_execution"
            if event.kind == "fill":
                return FillEvent(
                    map_execution_fill(event.payload), metadata
                ), "on_execution"
            raise ValueError(f"unsupported Execution event kind: {event.kind}")
        raise ValueError(f"unsupported strategy event domain: {event.domain}")

    def _scoped_account_id(self, payload: object) -> AccountId:
        if not isinstance(payload, Mapping):
            raise ValueError("Account-scoped event payload must be an object")
        nested = payload.get("snapshot", payload.get("status", payload))
        value = nested if isinstance(nested, Mapping) else payload
        raw = value.get("account_id", payload.get("account_id"))
        if not isinstance(raw, str) or not raw.strip():
            if len(self.clients.account_projections) == 1:
                raw = str(next(iter(self.clients.account_projections)))
            else:
                raise ValueError("Account-scoped event is missing account_id")
        account_id = AccountId(raw)
        if (
            self.clients.account_projections
            and account_id not in self.clients.account_projections
        ):
            raise PermissionError(
                f"account event {raw!r} is outside this strategy launch scope"
            )
        return account_id

    def _apply_backtest_callbacks(self, event: RawEventEnvelope) -> None:
        if self.clients.backtest_market is not None:
            result = self.clients.backtest_market(event)
            if isinstance(result, Mapping):
                fills = result.get("fills", ())
                if isinstance(fills, list):
                    self.backtest_fills.extend(
                        fill for fill in fills if isinstance(fill, Mapping)
                    )
        self._apply_backtest_account_mark(event)

    def _apply_backtest_account_mark(self, event: RawEventEnvelope) -> None:
        if self.clients.backtest_account_mark is None:
            return
        try:
            mark_result = self.clients.backtest_account_mark(event)
            if (
                isinstance(mark_result, Mapping)
                and mark_result.get("snapshot") is not None
            ):
                self.equity_curve.append(
                    {
                        "observed_at_unix_nanos": getattr(
                            event.payload, "event_time_unix_nanos", 0
                        ),
                        "snapshot": mark_result["snapshot"],
                    }
                )
        except RuntimeError as error:
            # A pre-position quote is valid replay input. Account starts
            # marking once the first simulated fill creates the position.
            if "not present in account" not in str(error):
                raise

    async def command(self, command: StrategyCommand) -> CommandResult:
        """Serialize an external command with the strategy lifecycle.

        Commands are handled by the same StrategyHost instance as market
        callbacks.  The optional hook may be synchronous for compatibility,
        but asynchronous handlers are the supported path for interactive
        Python code.
        """
        if self._status.state not in {
            StrategyLifecycle.READY,
            StrategyLifecycle.RUNNING,
            StrategyLifecycle.PAUSED,
        }:
            return CommandResult(
                command.request_id,
                "rejected",
                error=f"strategy is not commandable in state {self._status.state.value}",
                error_code="strategy_not_commandable",
            )
        callback = getattr(self.strategy, "on_command", None)
        if callback is None:
            return CommandResult(
                command.request_id,
                "rejected",
                error=f"unsupported strategy command: {command.kind}",
                error_code="unsupported_command",
            )
        self._command_active = True
        try:
            try:
                result = callback(self.context._bind(None), command)
                if inspect.isawaitable(result):
                    result = await asyncio.wait_for(
                        result, timeout=COMMAND_TIMEOUT_SECONDS
                    )
                if result is None:
                    return CommandResult(command.request_id, "completed")
                if not isinstance(result, CommandResult):
                    raise TypeError("strategy on_command must return CommandResult")
                return result
            except Exception as error:
                self._log(
                    "strategy command failed",
                    event="strategy_command_failed",
                    request_id=command.request_id,
                    command_kind=command.kind,
                    error=str(error),
                )
                return CommandResult(
                    command.request_id,
                    "failed",
                    error=str(error),
                    error_code=type(error).__name__,
                )
        finally:
            self._command_active = False
            while (
                self._queued_events and self._status.state is StrategyLifecycle.RUNNING
            ):
                self._dispatch_event(self._queued_events.popleft())

    async def run(self) -> None:
        """Consume the instance event stream after launch has enabled the strategy."""
        # Keep the transport-dependent exception lazy; importing it at module
        # load time would create a strategy-package initialization cycle.
        from kairospy.infrastructure.transport.market import EventStreamGap

        if self._status.state is not StrategyLifecycle.RUNNING:
            raise RuntimeError("strategy event loop requires a running strategy")
        self._stop_requested.clear()
        if self.clients.application_events:
            await self._run_multiplexed()
            return

        # Replay streams are finite.  Materializing that finite source gives
        # the replay driver visibility of the next market timestamp, so it can
        # choose every timer in a market gap without sleeping on wall time.
        # Live streams retain the reconnecting incremental path below.
        if getattr(self.clients.market_events, "replayable", False):
            try:
                replay_events = [
                    event
                    async for event in self.clients.market_events.events(
                        after_sequence=self._status.event_sequence
                    )
                ]
                for event in replay_events:
                    if self._stop_requested.is_set():
                        return
                    self._dispatch_replay_event(event)
                if self._replay_end is not None:
                    self._advance_replay_time(self._replay_end)
                self.stop()
                return
            except EventStreamGap as error:
                self._log(
                    "market event gap detected; recovering from snapshot",
                    event="market_event_gap",
                    expected=error.expected,
                    actual=error.actual,
                )
                if not self._recover_snapshot():
                    await asyncio.sleep(0.25)
                return
        while not self._stop_requested.is_set():
            try:
                async for event in self.clients.market_events.events(
                    after_sequence=self._status.event_sequence
                ):
                    if self._stop_requested.is_set():
                        return
                    self._dispatch_replay_event(event)
            except EventStreamGap as error:
                self._log(
                    "market event gap detected; recovering from snapshot",
                    event="market_event_gap",
                    expected_sequence=error.expected,
                    actual_sequence=error.actual,
                )
                if self._recover_snapshot():
                    continue
                await asyncio.sleep(0.25)
            except Exception as error:
                self._log(
                    "strategy event loop failed",
                    event="strategy_event_loop_failed",
                    error=repr(error),
                )
                self._release_subscriptions_best_effort()
                self._transition(StrategyLifecycle.FAILED, str(error))
                raise
            else:
                if self._replay_end is not None:
                    self._advance_replay_time(self._replay_end)
                self.stop()
                return

    async def _run_multiplexed(self) -> None:
        """Serially dispatch independently ordered business application streams."""
        streams = (self.clients.market_events, *self.clients.application_events)
        queue: asyncio.Queue[tuple[object, RawEventEnvelope | None]] = asyncio.Queue()

        async def pump(stream) -> None:
            try:
                after = self._stream_sequences.get(stream.stream_id, 0)
                async for event in stream.events(after_sequence=after):
                    await queue.put((stream, event))
            except Exception as error:
                await queue.put((error, None))
            else:
                await queue.put((stream, None))

        tasks = [asyncio.create_task(pump(stream)) for stream in streams]
        completed = 0
        try:
            while completed < len(streams) and not self._stop_requested.is_set():
                source, event = await queue.get()
                if isinstance(source, Exception):
                    raise source
                if event is None:
                    completed += 1
                    continue
                self.dispatch(event)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        if completed == len(streams):
            if self._replay_end is not None:
                self._advance_replay_time(self._replay_end)
            self.stop()

    def stop(self) -> StrategyHostStatus:
        if self._status.state in {
            StrategyLifecycle.STOPPED,
            StrategyLifecycle.STOPPING,
        }:
            return self._status
        self._stop_requested.set()
        self._transition(StrategyLifecycle.STOPPING)
        if (
            self._pending_bar_event is not None
            and self.clients.backtest_account_mark is not None
        ):
            try:
                self.clients.backtest_account_mark(self._pending_bar_event)
            except RuntimeError as error:
                if "not present in account" not in str(error):
                    raise
            self._pending_bar_event = None
        elif self._last_data_event is not None:
            # Quote replays execute the final event's orders after the
            # strategy callback.  Capture the Account state after that fill,
            # otherwise the report would end at the pre-fill mark.
            self._apply_backtest_account_mark(self._last_data_event)
        callback_error: Exception | None = None
        try:
            self._call("on_end", self.context._bind(None))
        except Exception as error:
            callback_error = error
        cleanup_error: Exception | None = None
        try:
            self._release_subscriptions()
        except Exception as error:
            cleanup_error = error
        checkpoint_error: Exception | None = None
        try:
            self.context.state.checkpoint()
        except Exception as error:
            checkpoint_error = error
        if (
            callback_error is not None
            or cleanup_error is not None
            or checkpoint_error is not None
        ):
            error = callback_error or cleanup_error or checkpoint_error
            assert error is not None
            reason = str(error)
            details = []
            if callback_error is not None:
                details.append(str(callback_error))
            if cleanup_error is not None:
                details.append(f"subscription cleanup failed: {cleanup_error}")
            if checkpoint_error is not None:
                details.append(f"state checkpoint failed: {checkpoint_error}")
            reason = "; ".join(details)
            self._transition(StrategyLifecycle.FAILED, reason)
            raise error
        self._transition(StrategyLifecycle.STOPPED)
        return self._status

    def close(self) -> None:
        """Release instance-owned capabilities on every process exit path."""
        self._stop_requested.set()
        self._release_subscriptions_best_effort()
        try:
            self.context.state.checkpoint()
        except Exception as error:
            self._log(
                "strategy state checkpoint failed during close",
                event="strategy_state_checkpoint_failed",
                error=str(error),
            )

    def _release_subscriptions(self) -> None:
        if self._subscription_owner_released:
            return
        handle = self.context.market._release_owner()
        request_id = handle.request_id
        if handle.status not in {"accepted", "completed", "removed", "ready"}:
            raise RuntimeError(
                handle.error
                or f"Market rejected subscription owner release: {handle.status}"
            )
        removed_value = handle.result.get("removed_subscription_ids", ())
        removed = removed_value if isinstance(removed_value, (list, tuple, set)) else ()
        removed_ids = {
            str(subscription_id)
            for subscription_id in removed
            if isinstance(subscription_id, str)
        }
        # A successful owner-scoped release is authoritative even when the
        # Market response omits individual IDs (for example an older fake).
        removed_ids.update(self._subscription_requests)
        for subscription_id in removed_ids:
            subscription = self._subscriptions.get(subscription_id)
            if subscription is not None:
                subscription["status"] = "removed"
                subscription["release_request_id"] = request_id
        self._subscription_requests.clear()
        self._subscription_owner_released = True
        self._status = replace(
            self._status,
            subscription_count=0,
            active_subscription_count=0,
            subscriptions=tuple(dict(value) for value in self._subscriptions.values()),
        )
        self._log(
            "market subscription owner released",
            event="market_subscription_owner_released",
            request_id=request_id,
            removed_subscription_ids=sorted(removed_ids),
        )

    def _release_subscriptions_best_effort(self) -> None:
        try:
            self._release_subscriptions()
        except Exception as error:
            self._log(
                "market subscription owner release failed",
                event="market_subscription_owner_release_failed",
                error=str(error),
            )

    def _refresh_dependencies(self) -> bool:
        results = {
            request_id: self.context.market._command_status(request_id)
            for request_id in self._subscription_requests
        }
        for request_id, result in results.items():
            subscription = self._subscriptions.setdefault(
                request_id, {"request_id": request_id}
            )
            subscription.update(
                {
                    "status": result.status,
                    "error": result.error,
                    "result": dict(result.result),
                }
            )
            self._log(
                "market subscription status observed",
                event="market_subscription_status",
                request_id=request_id,
                subscription_status=result.status,
                error=result.error,
                result=dict(result.result),
            )
        pending = [
            request_id
            for request_id, result in results.items()
            if result.status not in {"ready", "accepted"}
        ]
        active = len(results) - len(pending)
        self._status = replace(
            self._status,
            readiness=StrategyReadiness.WAITING_FOR_DEPENDENCIES
            if pending
            else StrategyReadiness.SUBSCRIPTIONS_ACTIVE,
            subscription_count=len(results),
            active_subscription_count=active,
            subscriptions=tuple(dict(value) for value in self._subscriptions.values()),
        )
        if pending:
            self._status = replace(
                self._status, reason=f"dependencies pending: {', '.join(pending)}"
            )
            return False
        else:
            self._status = replace(
                self._status,
                reason=None,
                readiness=StrategyReadiness.SUBSCRIPTIONS_ACTIVE,
            )
            self._log(
                "market subscriptions active",
                event="market_subscriptions_active",
                subscription_count=len(results),
            )
            return True

    def _bootstrap(self) -> bool:
        for view_key in self.snapshot_views:
            try:
                snapshot = self.clients.market_snapshots.read(view_key)
            except (FileNotFoundError, KeyError):
                self._status = replace(
                    self._status, reason=f"snapshot pending: {view_key}"
                )
                return False
            if snapshot.event_stream_id != self.clients.market_events.stream_id:
                raise RuntimeError(
                    "snapshot event stream does not match strategy event stream"
                )
            if not self.clients.market_events.can_join(snapshot.event_sequence):
                raise RuntimeError(
                    "snapshot watermark cannot be joined to event stream"
                )
            self._status = replace(self._status, event_sequence=snapshot.event_sequence)
            self._log(
                "strategy snapshot ready",
                event="snapshot_ready",
                view_key=view_key,
                snapshot_id=snapshot.snapshot_id,
                snapshot_sequence=snapshot.event_sequence,
            )
        self._status = replace(self._status, readiness=StrategyReadiness.SNAPSHOT_READY)
        return True

    def _recover_snapshot(self) -> bool:
        """Re-establish the event join point from the latest Market snapshot."""
        try:
            if not self._bootstrap():
                return False
        except (FileNotFoundError, KeyError, RuntimeError, ValueError) as error:
            self._log(
                "market snapshot recovery is pending",
                event="market_snapshot_recovery_pending",
                error=str(error),
            )
            return False
        self._status = replace(
            self._status,
            readiness=StrategyReadiness.SNAPSHOT_READY,
            data_health=StrategyDataHealth.WAITING_FOR_DATA,
        )
        self._log(
            "market snapshot recovery completed",
            event="market_snapshot_recovered",
            event_sequence=self._status.event_sequence,
        )
        return True

    def _call(self, name: str, *args: object) -> None:
        callback = getattr(self.strategy, name, None)
        if callback is None:
            return
        result = callback(*args)
        if result is not None:
            raise TypeError(
                f"{name} must return None; use context bus to interact with the system"
            )

    def _transition(self, state: StrategyLifecycle, reason: str | None = None) -> None:
        self._status = replace(self._status, state=state, reason=reason)
        self.journal.append(
            LifecycleRecord(
                self.launch_id,
                self.instance_id,
                self.strategy.strategy_id,
                state.value,
                reason,
                self._status.event_sequence,
                self._status.readiness.value,
                self._status.data_health.value,
            )
        )
        self._log(
            f"strategy state={state.value} event_sequence={self._status.event_sequence}"
            + (f" reason={reason}" if reason else "")
        )

    def _observe_subscription(self, request: object, handle: object) -> None:
        request_id = getattr(handle, "request_id", None)
        if not request_id:
            return
        self._subscription_requests.add(request_id)
        self._log(
            f"market subscription requested request_id={request_id}",
            event="market_subscription_requested",
            request_id=request_id,
            subject=getattr(request, "subject", None),
            exchange=getattr(request, "exchange", None),
            market_type=getattr(request, "market_type", None),
            asset_type=getattr(request, "asset_type", None),
            selectors=list(getattr(request, "selectors", ())),
            params=dict(getattr(request, "params", {})),
        )
        self._subscriptions[request_id] = {
            "request_id": request_id,
            "status": getattr(handle, "status", "unknown"),
            "subject": getattr(request, "subject", None),
            "exchange": getattr(request, "exchange", None),
            "market_type": getattr(request, "market_type", None),
            "asset_type": getattr(request, "asset_type", None),
            "selectors": list(getattr(request, "selectors", ())),
            "params": dict(getattr(request, "params", {})),
        }

    def _log(self, message: str, **data: object) -> None:
        self.logger.info(message, **data)
