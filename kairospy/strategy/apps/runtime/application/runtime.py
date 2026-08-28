from __future__ import annotations

from dataclasses import dataclass, replace
import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Mapping, cast

from ..domain.lifecycle import StrategyDataHealth, StrategyLifecycle, StrategyReadiness
from ..domain.messages import LifecycleRecord
from ..protocol import Strategy
from kairospy.strategy.apps.decisions.application import StrategyDecisionApplication
from kairospy.strategy.apps.decisions.services import StrategyDecisionJournal
from ..services.journal import StrategyLifecycleJournal
from kairospy.investment.apps.account.application import AccountApplication
from kairospy.strategy.apps.agent.application import AgentApplication
from kairospy.investment.apps.capital.application import (
    CapitalApplication,
    CapitalDemand,
    FundingLocation,
    FundingObjectiveStatus,
    FundingPriority,
)
from kairospy.investment.apps.execution.application import (
    ExecutionApplication,
    ExecutionBacktestResult,
    Fill,
)
from kairospy.strategy.api.execution import ExecutionEvent
from kairospy.investment.apps.market.application import MarketApplication
from kairospy.strategy.apps.notification.application import NotificationApplication
from kairospy.investment.apps.portfolio.application import PortfolioApplication
from kairospy.investment.apps.reference.application import ReferenceApplication
from kairospy.investment.apps.risk.application import RiskApplication
from kairospy.primitives.account import AccountId, BrokerId, SegmentKey
from kairospy.primitives.capital import CapitalDemandId
from kairospy.primitives.decimal import Quantity
from kairospy.primitives.reference import AssetId
from kairospy.primitives.runtime import IdempotencyKey, RequestId
from kairospy.primitives.time import Sequence
from ..services.context import StrategyContext
from ..services.callbacks import StrategyCallbackHost
from ..services.ingress import StrategyEventIngress, StrategySourceError
from kairospy.strategy.api import (
    EventMetadata,
    MarketEvent,
    StrategyLogger,
    TimerFiredEvent,
    SystemEvent,
    SystemNotice,
)
from kairospy.strategy.api import CommandResult, StrategyCommand
from kairospy.contracts.market.types import MarketSubscriptionResponse
from kairospy.strategy.api.clock import (
    DeterministicTimerQueue,
    StrategyClock,
    TimerEvent,
    ensure_utc,
)


@dataclass(frozen=True, slots=True)
class StrategyStatus:
    launch_id: str
    instance_id: str
    strategy_id: str
    state: StrategyLifecycle
    reason: str | None = None
    dispatch_sequence: int = 0
    readiness: StrategyReadiness = StrategyReadiness.NOT_STARTED
    data_health: StrategyDataHealth = StrategyDataHealth.NOT_STARTED
    subscription_count: int = 0
    active_subscription_count: int = 0
    first_event_received: bool = False
    last_event_time: datetime | None = None
    last_event_kind: str | None = None
    event_count: int = 0
    subscriptions: tuple[Mapping[str, object], ...] = ()


class StrategyApplication:
    """Application facade for one instance-owned user Strategy runtime.

    Launch owns this application's process lifecycle. Business state remains
    owned by Market, Account, Portfolio, Risk, and Execution applications.
    """

    def __init__(
        self,
        strategy: Strategy,
        *,
        launch_id: str,
        instance_id: str,
        reference: ReferenceApplication,
        market: MarketApplication,
        account: AccountApplication,
        risk: RiskApplication,
        execution: ExecutionApplication,
        portfolio: PortfolioApplication | None = None,
        capital: CapitalApplication | None = None,
        agent: AgentApplication | None = None,
        agent_events=None,
        agent_synchronize=None,
        notifications: NotificationApplication | None = None,
        decision_journal: StrategyDecisionJournal | None = None,
        decision_notification_routes: tuple[str, ...] = (),
        journal: StrategyLifecycleJournal,
        state_path=None,
        backtest=None,
        params: Mapping[str, object] | None = None,
        logger: StrategyLogger | None = None,
        replay_end: datetime | None = None,
    ) -> None:
        if not launch_id.strip() or not instance_id.strip():
            raise ValueError("launch_id and instance_id are required")
        self.strategy = strategy
        self.launch_id = launch_id
        self.instance_id = instance_id
        self.backtest = backtest
        self._agent_synchronize = agent_synchronize
        self._synchronizing_agent = False
        self.journal = journal
        self.logger = logger or StrategyLogger(
            fields={
                "launch_id": launch_id,
                "instance_id": instance_id,
                "strategy_id": strategy.strategy_id,
                "component": "strategy",
            }
        )
        self.context = StrategyContext(
            strategy.strategy_id,
            reference=reference,
            market=market,
            account=account,
            portfolio=portfolio
            or PortfolioApplication(f"{launch_id}:{instance_id}", account),
            capital=capital
            or CapitalApplication.disabled(
                strategy_id=strategy.strategy_id,
                launch_id=launch_id,
                instance_id=instance_id,
            ),
            risk=risk,
            execution=execution,
            agent=agent,
            notifications=notifications,
            launch_id=launch_id,
            instance_id=instance_id,
            params=params,
            state_path=state_path,
            logger=self.logger,
        )
        self.ingress = StrategyEventIngress(
            market=market,
            account=account,
            risk=risk,
            execution=execution,
            agent_events=agent_events,
        )
        self.callbacks = StrategyCallbackHost(strategy, self.context, self.logger)
        self._status = StrategyStatus(
            launch_id, instance_id, strategy.strategy_id, StrategyLifecycle.CREATED
        )
        self._subscription_requests: set[RequestId] = set()
        self._subscriptions: dict[RequestId, dict[str, object]] = {}
        self._subscription_owner_released = False
        self._stop_requested = asyncio.Event()
        self._command_active = False
        self._queued_events: deque[object] = deque(maxlen=256)
        self._timers = DeterministicTimerQueue()
        self._timer_sequence = 0
        self._system_sequence = 0
        self._clock = StrategyClock(self._timers.schedule, self._timers.cancel)
        self.context.clock = self._clock
        self.decisions = StrategyDecisionApplication(
            strategy_id=strategy.strategy_id,
            instance_id=instance_id,
            journal=decision_journal
            or StrategyDecisionJournal(
                (
                    getattr(journal, "path").parent / "strategy-decisions.jsonl"
                    if getattr(journal, "path", None) is not None
                    else None
                )
            ),
            notifications=self.context.notifications,
            clock=self._clock,
            notification_routes=decision_notification_routes,
        )
        self.context.decisions = self.decisions
        self.context.execution.bind_decisions(self.decisions)
        self._replay_end = replay_end
        self._pending_bar_event: MarketEvent | None = None
        self._last_data_event: MarketEvent | None = None
        self.clock_events: list[dict[str, object]] = []
        self.event_trace: list[dict[str, object]] = []
        self._trace_sequence = 0
        self._stream_sequences: dict[str, int] = {}
        self.backtest_fills: list[Fill] = []
        self._log("strategy application created", event="strategy_application_created")

    @property
    def status(self) -> StrategyStatus:
        return self._status

    @property
    def equity_curve(self) -> list[dict[str, object]]:
        """Compatibility report view now owned by PortfolioApplication."""

        return self.context.portfolio.equity_curve

    def decision_trace(self, strategy_decision_id: str) -> dict[str, object] | None:
        """Aggregate Strategy progress with authoritative Execution and delivery facts."""

        trace = self.decisions.trace(strategy_decision_id)
        if trace is None:
            return None
        execution = trace.get("execution")
        if isinstance(execution, dict):
            intents = execution.get("intents")
            if isinstance(intents, list):
                for intent in intents:
                    if not isinstance(intent, dict):
                        continue
                    intent_id = intent.get("intent_id")
                    if not isinstance(intent_id, str):
                        continue
                    authoritative = self.context.execution.diagnostic_intent(intent_id)
                    intent["authoritative_execution"] = authoritative
        notifications = trace.get("notifications")
        notification_ids = (
            tuple(
                notification_id
                for record in notifications
                if isinstance(record, Mapping)
                and isinstance(notification_id := record.get("notification_id"), str)
                and notification_id
            )
            if isinstance(notifications, list)
            else ()
        )
        trace["notification_deliveries"] = list(
            self.context.notifications.deliveries(notification_ids)
        )
        return trace

    def start(self) -> StrategyStatus:
        if self._status.state is not StrategyLifecycle.CREATED:
            raise RuntimeError(
                f"strategy can only start from created: {self._status.state}"
            )
        self._transition(StrategyLifecycle.WAITING_FOR_DEPENDENCIES)
        self._log("strategy startup begin", event="strategy_starting")
        self._log("strategy on_start begin", event="strategy_on_start_begin")
        try:
            self.callbacks.lifecycle("on_start")
        except Exception as error:
            self._release_subscriptions_best_effort()
            self._transition(StrategyLifecycle.FAILED, str(error))
            raise
        try:
            dependencies_ready = self._refresh_dependencies()
            self._log(
                "strategy on_start completed "
                f"subscriptions={len(self._subscription_requests)}",
                event="strategy_on_start_completed",
                subscription_count=len(self._subscription_requests),
            )
            if not dependencies_ready:
                self._log(
                    f"waiting for dependencies reason={self._status.reason}",
                    event="dependencies_waiting",
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

    def enable(self) -> StrategyStatus:
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

    def pause(self, reason: str = "paused by control") -> StrategyStatus:
        if self._status.state is not StrategyLifecycle.RUNNING:
            raise RuntimeError(
                f"strategy can only be paused from running: {self._status.state}"
            )
        self._transition(StrategyLifecycle.PAUSED, reason)
        return self._status

    def resume(self) -> StrategyStatus:
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

    def refresh(self) -> StrategyStatus:
        if self._status.state is not StrategyLifecycle.WAITING_FOR_DEPENDENCIES:
            return self._status
        try:
            if not self._refresh_dependencies():
                return self._status
        except Exception as error:
            self._release_subscriptions_best_effort()
            self._transition(StrategyLifecycle.FAILED, str(error))
            raise
        self._status = replace(self._status, readiness=StrategyReadiness.READY)
        self._transition(StrategyLifecycle.READY)
        self._log("strategy startup ready", event="strategy_ready")
        return self._status

    def dispatch(self, event: object) -> None:
        """Dispatch an already typed business event to the user Strategy."""

        if self._command_active:
            if len(self._queued_events) == self._queued_events.maxlen:
                raise RuntimeError("strategy command event queue overflowed")
            self._queued_events.append(event)
            return
        domain = self.ingress.route(event).domain
        metadata = getattr(event, "metadata")
        occurred_at = _metadata_datetime(metadata)
        if domain in {"market", "clock"} and occurred_at is not None:
            event_time = ensure_utc(occurred_at)
            if self._clock.now is None or event_time >= self._clock.now:
                self.advance_time(event_time)
        self._dispatch_event(event)

    def _dispatch_replay_event(self, event: MarketEvent) -> None:
        """Merge due strategy timers before the next replay observation.

        The market stream remains the source of observations, but the virtual
        clock is allowed to visit timer timestamps inside a data gap.  Clock
        events at the same timestamp are emitted before the market event.
        """
        occurred_at = _metadata_datetime(event.metadata)
        if occurred_at is None:
            self._dispatch_event(event)
            return
        event_time = ensure_utc(occurred_at)
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
        if self.backtest is not None:
            event_time_unix_nanos = int(current.timestamp() * 1_000_000_000)
            self.backtest.advance_time(event_time_unix_nanos)
        for timer in self._timers.pop_due(current):
            self._dispatch_timer(timer)

    def _dispatch_timer(self, timer: TimerEvent) -> None:
        self.decisions.observe_timer(timer)
        self._timer_sequence = (
            max(self._timer_sequence, self._status.dispatch_sequence) + 1
        )
        event = TimerFiredEvent(
            timer,
            EventMetadata(
                stream_id=f"strategy.clock:{self.instance_id}",
                sequence=self._timer_sequence,
                producer="strategy.clock",
                occurred_at=timer.event_time,
                occurred_at_unix_nanos=int(
                    timer.event_time.timestamp() * 1_000_000_000
                ),
            ),
        )
        self.clock_events.append(
            {
                "timer_id": timer.timer_id,
                "scheduled_at": timer.scheduled_at,
                "event_time": timer.event_time,
                "sequence": event.metadata.sequence,
                "trace_sequence": self._trace_sequence + 1,
            }
        )
        self._dispatch_event(event)

    def _dispatch_event(self, event: object) -> None:
        if self._status.state is not StrategyLifecycle.RUNNING:
            return
        route = self.ingress.route(event)
        if route.domain == "execution" and getattr(event, "kind") in {
            "intent_accepted",
            "intent_rejected",
            "intent_lifecycle_changed",
            "fill_recorded",
        }:
            self.decisions.observe_execution(cast(ExecutionEvent, event))
        domain, hook = route.domain, route.hook
        metadata = getattr(event, "metadata")
        previous_sequence = self._stream_sequences.get(metadata.stream_id, 0)
        # One module contract record may map to several typed business
        # callbacks (for example balance + equity). The module Application
        # already validates and deduplicates record continuity, so equal
        # source sequences are valid siblings; only regression is invalid.
        if metadata.sequence < previous_sequence:
            raise ValueError(
                f"event stream {metadata.stream_id} regressed: "
                f"previous={previous_sequence}, received={metadata.sequence}"
            )
        self._stream_sequences[metadata.stream_id] = max(
            previous_sequence, metadata.sequence
        )
        # Portfolio is the instance-owned consolidated record. It observes
        # Account/Market facts before user callbacks see the same event.
        self.context.portfolio.observe(event)
        self._observe_execution_funding_demand(event)
        self._trace_sequence += 1
        self.event_trace.append(
            {
                "trace_sequence": self._trace_sequence,
                "domain": domain,
                "kind": getattr(event, "kind"),
                "event_time": _metadata_datetime(metadata),
                "source_sequence": metadata.sequence,
                "source_stream_id": metadata.stream_id,
            }
        )

        # Source continuity belongs to the Market event contract. Strategy
        # records received source metadata but never reads a snapshot to join
        # or repair the stream.
        # A completed bar can only be used for execution on the next market
        # event.  This prevents a strategy from observing a bar close and
        # immediately filling against that same close by accident.  Quote
        # events keep the existing quote-after-intent behavior.
        if domain == "market" and getattr(event, "kind") == "bar_completed":
            market_event = cast(MarketEvent, event)
            if self._pending_bar_event is not None:
                self._apply_backtest_callbacks(self._pending_bar_event)
            self._pending_bar_event = market_event
            self._last_data_event = market_event
        elif domain == "market" and getattr(event, "kind") in {
            "quote_updated",
            "trade_occurred",
        }:
            self._last_data_event = cast(MarketEvent, event)

        def log_dispatch() -> None:
            if hook == "on_market":
                if getattr(self.strategy, "log_on_market", False):
                    self._log(
                        "strategy on_market event",
                        event="strategy_on_market",
                        event_domain=domain,
                        event_kind=getattr(event, "kind"),
                        event_payload=_market_event_log_value(getattr(event, "data")),
                    )
            else:
                self._log(f"dispatch {hook}", event_kind=getattr(event, "kind"))

        try:
            self.callbacks.dispatch(hook, domain, event, on_bound=log_dispatch)
        except Exception as error:
            self._release_subscriptions_best_effort()
            self._transition(StrategyLifecycle.FAILED, str(error))
            raise
        if self.backtest is not None:
            self._synchronize_agent_events()
        if domain == "market" and getattr(event, "kind") in {
            "quote_updated",
            "trade_occurred",
        }:
            self._apply_backtest_callbacks(cast(MarketEvent, event))
        first_event = not self._status.first_event_received and domain == "market"
        self._status = replace(
            self._status,
            dispatch_sequence=(
                self._status.dispatch_sequence
                if domain == "clock"
                else self._trace_sequence
            ),
            data_health=(
                StrategyDataHealth.HEALTHY
                if domain == "market"
                else self._status.data_health
            ),
            first_event_received=(
                True if domain == "market" else self._status.first_event_received
            ),
            last_event_time=_metadata_datetime(metadata),
            last_event_kind=getattr(event, "kind"),
            event_count=self._status.event_count + 1,
        )
        if first_event:
            self._log(
                "first strategy data event received",
                event="first_data_event_received",
                event_kind=getattr(event, "kind"),
                event_sequence=metadata.sequence,
            )

    def _observe_execution_funding_demand(self, event: object) -> None:
        metadata = getattr(event, "metadata", None)
        if (
            not self.context.capital.enabled
            or getattr(metadata, "stream_id", None) != "execution.events"
            or getattr(event, "kind") != "order_rejected"
        ):
            return
        data = getattr(event, "data")
        if str(getattr(data, "status")) != "rejected":
            return
        order_id = str(getattr(data, "order_id"))
        account_id = AccountId(str(getattr(data, "account_id")))
        reservation = next(
            (
                value
                for value in self.context.execution.risk_reservations()
                if str(value.order_id) == order_id
            ),
            None,
        )
        if reservation is None or reservation.funding_requirement is None:
            return
        requirement = reservation.funding_requirement
        lease_fence = self.context.capital.account_lease_fence(account_id)
        if lease_fence is None:
            self._log(
                "capital demand omitted because account lease fence is unavailable",
                event="capital_demand_omitted",
                order_id=order_id,
            )
            return
        occurred_at_unix_nanos = getattr(metadata, "occurred_at_unix_nanos", None)
        if occurred_at_unix_nanos is None:
            self._log(
                "capital demand omitted because event time is unavailable",
                event="capital_demand_omitted",
                order_id=order_id,
            )
            return
        observed_at = datetime.fromtimestamp(
            occurred_at_unix_nanos / 1_000_000_000,
            tz=timezone.utc,
        )
        demand_id = CapitalDemandId(
            f"risk:{requirement.risk_decision_id}:{order_id}"
        )
        try:
            receipt = self.context.capital.observe_demand(
                CapitalDemand(
                    demand_id=demand_id,
                    idempotency_key=IdempotencyKey(str(demand_id)),
                    destination=FundingLocation(
                        account_id,
                        SegmentKey(requirement.segment),
                        AssetId(str(requirement.collateral_asset)),
                        BrokerId(str(requirement.broker)),
                    ),
                    observed_shortfall=Quantity(requirement.shortfall),
                    observed_at=observed_at,
                    required_by=observed_at,
                    expires_at=observed_at + timedelta(seconds=60),
                    account_watermark=Sequence(
                        requirement.account_snapshot_watermark
                    ),
                    risk_watermark=Sequence(
                        max(
                            reservation.risk_generation,
                            reservation.risk_event_sequence,
                        )
                    ),
                    destination_lease_fence=lease_fence,
                    priority=FundingPriority.HIGH,
                    causal_references=(
                        f"execution-order:{order_id}",
                        f"risk-decision:{requirement.risk_decision_id}",
                    ),
                )
            )
        except Exception as error:
            self._log(
                "capital demand observation failed",
                event="capital_demand_failed",
                order_id=order_id,
                error=str(error),
            )
            return
        if receipt.status not in {
            FundingObjectiveStatus.ACCEPTED,
            FundingObjectiveStatus.DUPLICATE,
        }:
            self._log(
                "capital demand was not accepted",
                event="capital_demand_not_accepted",
                demand_id=demand_id,
                status=receipt.status.value,
                reason=receipt.message,
            )

    def _synchronize_agent_events(self) -> None:
        synchronize = self._agent_synchronize
        if synchronize is None or self._synchronizing_agent:
            return
        self._synchronizing_agent = True
        try:
            for _ in range(256):
                events = tuple(synchronize())
                if not events:
                    return
                for event in events:
                    self._dispatch_event(event)
            raise RuntimeError("Backtest Agent event cascade exceeded 256 batches")
        finally:
            self._synchronizing_agent = False

    def _apply_backtest_callbacks(self, event: MarketEvent) -> None:
        if self.backtest is not None:
            result = self.backtest.apply_market(event)
            if not isinstance(result, ExecutionBacktestResult):
                raise TypeError(
                    "StrategyBacktestDriver.apply_market must return "
                    "ExecutionBacktestResult"
                )
            self.backtest_fills.extend(result.fills)
        self._apply_backtest_account_mark(event)

    def _apply_backtest_account_mark(self, event: MarketEvent) -> None:
        if self.backtest is None:
            return
        try:
            snapshot = self.backtest.mark_account(event)
            if snapshot is not None:
                self.context.portfolio.record_account_mark(
                    snapshot,
                    observed_at_unix_nanos=getattr(
                        event.data, "occurred_at_unix_nanos", 0
                    ),
                )
        except RuntimeError as error:
            # A pre-position quote is valid replay input. Account starts
            # marking once the first simulated fill creates the position.
            if "not present in account" not in str(error):
                raise

    async def command(self, command: StrategyCommand) -> CommandResult:
        """Serialize an external command with the strategy lifecycle.

        Commands are handled by the same StrategyApplication instance as market
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
        self._command_active = True
        try:
            try:
                return await self.callbacks.command(command)
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
        if self._status.state is not StrategyLifecycle.RUNNING:
            raise RuntimeError("strategy event loop requires a running strategy")
        self._stop_requested.clear()
        # Replay streams are finite.  Materializing that finite source gives
        # the replay driver visibility of the next market timestamp, so it can
        # choose every timer in a market gap without sleeping on wall time.
        # Live streams retain the reconnecting incremental path below.
        if self.context.market.events_replayable:
            try:
                replay_events = [
                    event async for event in self.context.market.replay_events()
                ]
                for event in replay_events:
                    if self._stop_requested.is_set():
                        return
                    self._dispatch_replay_event(event)
                if self._replay_end is not None:
                    self._advance_replay_time(self._replay_end)
                self.stop()
                return
            except Exception as error:
                wrapped = StrategySourceError("market", error)
                self._emit_system_fact_best_effort(
                    "event_source_failed",
                    "market event source failed",
                    {"domain": "market", "error": str(error)},
                )
                self._release_subscriptions_best_effort()
                self._transition(StrategyLifecycle.FAILED, str(wrapped))
                raise wrapped from error
        self.ingress.start_owned()
        try:
            while not self._stop_requested.is_set():
                try:
                    if self._command_active:
                        await asyncio.sleep(0.001)
                        continue
                    owned_count = self.ingress.drain_owned(
                        lambda dispatch: self.dispatch(dispatch.event)
                    )
                    summary = self.ingress.poll_once(
                        lambda dispatch: self.dispatch(dispatch.event),
                        include_market=self.context.market.events_enabled,
                    )
                    if summary.fragment_count == 0 and owned_count == 0:
                        try:
                            await asyncio.wait_for(
                                self._stop_requested.wait(), timeout=0.001
                            )
                        except TimeoutError:
                            pass
                    else:
                        await asyncio.sleep(0)
                except Exception as error:
                    domain = getattr(error, "domain", "unknown")
                    self._emit_system_fact_best_effort(
                        "event_source_failed",
                        f"{domain} event source failed",
                        {
                            "domain": str(domain),
                            "error": str(getattr(error, "cause", error)),
                        },
                    )
                    self._log(
                        "strategy event loop failed",
                        event="strategy_event_loop_failed",
                        error=repr(error),
                    )
                    self._release_subscriptions_best_effort()
                    self._transition(StrategyLifecycle.FAILED, str(error))
                    raise
        finally:
            await self.ingress.close()

    def stop(self) -> StrategyStatus:
        if self._status.state in {
            StrategyLifecycle.STOPPED,
            StrategyLifecycle.STOPPING,
        }:
            return self._status
        self._stop_requested.set()
        if self._status.state is StrategyLifecycle.RUNNING:
            self._emit_system_fact_best_effort(
                "strategy_shutdown",
                "Strategy event processing is stopping",
                {"reason": "stop_requested"},
            )
        self._transition(StrategyLifecycle.STOPPING)
        if self._pending_bar_event is not None and self.backtest is not None:
            try:
                self.backtest.mark_account(self._pending_bar_event)
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
            self.callbacks.lifecycle("on_end")
            if self.backtest is not None:
                # on_end may submit a fixture-governed Intent. Reach the same
                # deterministic worker barrier used after ordinary callbacks
                # before the backtest report is written.
                self._synchronize_agent_events()
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

    def _emit_system_fact_best_effort(
        self, code: str, message: str, details: Mapping[str, str]
    ) -> None:
        """Record and dispatch a Strategy-owned runtime fact before transition."""

        self._system_sequence += 1
        event = SystemEvent(
            SystemNotice(code, message, details),
            EventMetadata(
                stream_id=f"strategy.system:{self.instance_id}",
                sequence=self._system_sequence,
                producer="strategy.application",
            ),
        )
        try:
            self._dispatch_event(event)
        except Exception as error:
            self._log(
                "strategy system fact callback failed",
                event="strategy_system_fact_failed",
                system_code=code,
                error=str(error),
            )

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
        result = self.context.market.release_strategy_subscriptions()
        request_id = result.request_id
        if result.status not in {"accepted", "applied", "completed", "removed", "ready"}:
            raise RuntimeError(
                result.error
                or f"Market rejected subscription owner release: {result.status}"
            )
        removed_ids = tuple(result.removed_subscription_ids)
        # Owner-scoped release is authoritative for every request owned by this
        # Strategy, regardless of whether the response enumerates every ID.
        for subscription in self._subscriptions.values():
            subscription["status"] = "removed"
            subscription["release_request_id"] = str(request_id)
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
            removed_subscription_ids=sorted(map(str, removed_ids)),
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
        # Readiness belongs to each concrete business Application. Strategy
        # neither opens Aeron itself nor consults indexed-view metadata.
        self.context.account._check_event_source_ready()
        self.context.portfolio.rebuild()
        if self.context.account.account_ids:
            self.context.portfolio.require_current()
        self.context.risk.check_event_source_ready()
        self.context.execution.check_event_source_ready()
        statuses = self.context.market.subscription_statuses()
        results = {status.request_id: status for status in statuses}
        # A Market event stream is enabled only when this Strategy owns Market
        # demand. Strategies with no Market subscription do not open a
        # decorative stream merely for interface symmetry.
        if results:
            self.context.market.check_event_source_ready()
        for request_id, result in results.items():
            if request_id not in self._subscription_requests:
                self._observe_subscription(result)
            subscription = self._subscriptions.setdefault(
                request_id, {"request_id": str(request_id)}
            )
            response = cast(MarketSubscriptionResponse, result.response)
            response_summary = {
                "subscription_id": str(response.subscription_id),
                "owner_id": str(response.owner_id),
                "state": response.state,
                "satisfied_selectors": list(response.satisfied_selectors),
                "missing_selectors": list(response.missing_selectors),
                "resolved_providers": list(response.resolved_providers),
                "pending_reason": response.pending_reason,
            }
            subscription.update(
                {
                    "status": result.status,
                    "error": result.error,
                    "response": response_summary,
                }
            )
            self._log(
                "market subscription status observed",
                event="market_subscription_status",
                request_id=request_id,
                subscription_status=result.status,
                error=result.error,
                response=response_summary,
            )
        pending = [
            request_id
            for request_id, result in results.items()
            if result.status != "active"
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
                self._status,
                reason=f"dependencies pending: {', '.join(map(str, pending))}",
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

    def _transition(self, state: StrategyLifecycle, reason: str | None = None) -> None:
        self._status = replace(self._status, state=state, reason=reason)
        self.journal.append(
            LifecycleRecord(
                self.launch_id,
                self.instance_id,
                self.strategy.strategy_id,
                state.value,
                reason,
                self._status.dispatch_sequence,
                self._status.readiness.value,
                self._status.data_health.value,
            )
        )
        self._log(
            f"strategy state={state.value} dispatch_sequence={self._status.dispatch_sequence}"
            + (f" reason={reason}" if reason else "")
        )

    def _observe_subscription(self, status: object) -> None:
        request_id = getattr(status, "request_id")
        request = getattr(status, "request")
        target = getattr(request, "target")
        observations = getattr(request, "observations", ())
        provider_preference = getattr(request, "provider_preference")
        target_value = _market_target_log_value(target)
        observation_values = [value.selector for value in observations]
        preference_value = {
            "mode": provider_preference.mode,
            "providers": list(provider_preference.providers),
        }
        self._subscription_requests.add(request_id)
        self._log(
            f"market subscription requested request_id={request_id}",
            event="market_subscription_requested",
            request_id=request_id,
            target=target_value,
            observations=observation_values,
            provider_preference=preference_value,
        )
        self._subscriptions[request_id] = {
            "request_id": str(request_id),
            "status": getattr(status, "status", "unknown"),
            "target": target_value,
            "observations": observation_values,
            "provider_preference": preference_value,
        }

    def _log(self, message: str, **data: object) -> None:
        self.logger.info(message, **data)


def _market_target_log_value(target: object) -> dict[str, object]:
    """Create a Strategy-owned diagnostic summary of a native Market target."""

    kind = str(getattr(target, "kind"))
    value: dict[str, object] = {"kind": kind}
    for name in (
        "market_id",
        "instrument_id",
        "network_id",
        "underlying_market_id",
        "underlying_instrument_id",
    ):
        field = getattr(target, name, None)
        if field is not None:
            value[name] = field
    return value


def _metadata_datetime(metadata: object) -> datetime | None:
    value = getattr(metadata, "occurred_at", None)
    if value is not None:
        return value
    nanos = getattr(metadata, "occurred_at_unix_nanos", None)
    if nanos is None:
        return None
    return datetime.fromtimestamp(int(nanos) / 1_000_000_000, tz=timezone.utc)


def _market_event_log_value(data: object) -> dict[str, object]:
    """Build a Strategy-owned diagnostic summary without copying the owner DTO."""

    value: dict[str, object] = {}
    for name in ("instrument_id", "provider", "bar_spec_id"):
        field = getattr(data, name, None)
        if field is not None:
            value[name] = field
    scope = getattr(data, "scope", None)
    if scope is not None:
        value["scope"] = _scope_log_value(scope)
    return value


def _scope_log_value(scope: object) -> dict[str, object]:
    value: dict[str, object] = {"kind": str(getattr(scope, "kind"))}
    for name in ("market_id", "instrument_id", "network_id"):
        field = getattr(scope, name, None)
        if field is not None:
            value[name] = field
    return value
