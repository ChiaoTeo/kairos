from __future__ import annotations

from dataclasses import dataclass, replace
import asyncio
from datetime import datetime
from typing import Mapping

from ..domain.lifecycle import StrategyDataHealth, StrategyLifecycle, StrategyReadiness
from ..domain.messages import EventEnvelope, LifecycleRecord
from ..protocol import LifecycleJournal, Strategy
from .context import StrategyClientBundle, StrategyContext
from kairospy.strategy import StrategyLogger


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
        logger: StrategyLogger | None = None,
        snapshot_views: tuple[str, ...] = ("market.current",),
    ) -> None:
        if not launch_id.strip() or not instance_id.strip():
            raise ValueError("launch_id and instance_id are required")
        self.strategy = strategy
        self.launch_id = launch_id
        self.instance_id = instance_id
        self.clients = clients
        # Transitional aliases for diagnostics and existing fixtures.  New
        # runtime code reads the explicit bundle above.
        self._bus = clients.commands
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
        self.context = StrategyContext(
            strategy.strategy_id,
            instance_id=instance_id,
            clients=clients,
            request_observer=self._observe_request,
            logger=self.logger,
        )
        self.snapshot_views = snapshot_views
        self._status = StrategyHostStatus(
            launch_id, instance_id, strategy.strategy_id, StrategyLifecycle.CREATED
        )
        self._subscription_requests: set[str] = set()
        self._subscriptions: dict[str, dict[str, object]] = {}
        self._stop_requested = asyncio.Event()
        self.equity_curve: list[dict[str, object]] = []
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
            self._transition(StrategyLifecycle.FAILED, str(error))
            raise
        self._log(
            f"strategy on_start completed subscriptions={len(self._subscription_requests)}",
            event="strategy_on_start_completed",
            subscription_count=len(self._subscription_requests),
        )
        if not self._refresh_dependencies():
            self._log(
                f"waiting for dependencies reason={self._status.reason}",
                event="dependencies_waiting",
            )
            return self._status
        try:
            if not self._bootstrap():
                self._log(
                    f"waiting for snapshot reason={self._status.reason}",
                    event="snapshot_waiting",
                )
                return self._status
        except Exception as error:
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
        if not self._refresh_dependencies():
            return self._status
        try:
            if not self._bootstrap():
                return self._status
        except Exception as error:
            self._transition(StrategyLifecycle.FAILED, str(error))
            raise
        self._status = replace(self._status, readiness=StrategyReadiness.READY)
        self._transition(StrategyLifecycle.READY)
        self._log("strategy startup ready", event="strategy_ready")
        return self._status

    def dispatch(self, event: EventEnvelope) -> None:
        if self._status.state is not StrategyLifecycle.RUNNING:
            return
        if event.stream_id != self.clients.market_events.stream_id:
            raise ValueError("event belongs to a different stream")
        # The live Unix stream has no replay/acknowledgement handshake, so a
        # subscriber can legitimately miss events while attaching.  The
        # snapshot supplies the initial state; thereafter the stream advances
        # the watermark to each received event without claiming replay-grade
        # continuity.
        self.context._bind(event)
        hook = {
            "data": "on_data",
            "intent": "on_intent",
            "clock": "on_clock",
            "system": "on_system",
        }.get(event.domain, "on_data")
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
            self._log(f"dispatch {hook}", event_kind=event.kind)
            try:
                self._call(hook, self.context, event)
            except Exception as error:
                self._transition(StrategyLifecycle.FAILED, str(error))
                raise
        if self.clients.backtest_market is not None and event.domain == "data":
            self.clients.backtest_market(event)
        if self.clients.backtest_account_mark is not None and event.domain == "data":
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
        first_event = not self._status.first_event_received
        self._status = replace(
            self._status,
            event_sequence=event.sequence,
            data_health=StrategyDataHealth.HEALTHY,
            first_event_received=True,
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

    async def run(self) -> None:
        """Consume the instance event stream after launch has enabled the strategy."""
        # Keep the transport-dependent exception lazy; importing it at module
        # load time would create a strategy-package initialization cycle.
        from kairospy.infrastructure.contracts.market import EventStreamGap

        if self._status.state is not StrategyLifecycle.RUNNING:
            raise RuntimeError("strategy event loop requires a running strategy")
        self._stop_requested.clear()
        while not self._stop_requested.is_set():
            try:
                async for event in self.clients.market_events.events(
                    after_sequence=self._status.event_sequence
                ):
                    if self._stop_requested.is_set():
                        return
                    self.dispatch(event)
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
            else:
                # A replay-capable stream may finish normally.  The current
                # live stream reconnects internally and does not reach here.
                self.stop()
                return

    def stop(self) -> StrategyHostStatus:
        if self._status.state in {
            StrategyLifecycle.STOPPED,
            StrategyLifecycle.STOPPING,
        }:
            return self._status
        self._stop_requested.set()
        self._transition(StrategyLifecycle.STOPPING)
        self._call("on_end", self.context._bind(None))
        self._transition(StrategyLifecycle.STOPPED)
        return self._status

    def _refresh_dependencies(self) -> bool:
        results = {
            request_id: self.clients.commands.status(request_id)
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
            self.context._install_snapshot(snapshot)
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

    def _observe_request(self, request: object, handle: object) -> None:
        if getattr(request, "operation", None) == "market.subscribe":
            request_id = getattr(handle, "request_id", None)
            if request_id:
                self._subscription_requests.add(request_id)
                payload = getattr(request, "payload", None)
                self._log(
                    f"market subscription requested request_id={request_id}",
                    event="market_subscription_requested",
                    request_id=request_id,
                    subject=getattr(payload, "subject", None),
                    exchange=getattr(payload, "exchange", None),
                    market_type=getattr(payload, "market_type", None),
                    asset_type=getattr(payload, "asset_type", None),
                    selectors=list(getattr(payload, "selectors", ())),
                    params=dict(getattr(payload, "params", {})),
                )
                self._subscriptions[request_id] = {
                    "request_id": request_id,
                    "status": getattr(handle, "status", "unknown"),
                    "subject": getattr(payload, "subject", None),
                    "exchange": getattr(payload, "exchange", None),
                    "market_type": getattr(payload, "market_type", None),
                    "asset_type": getattr(payload, "asset_type", None),
                    "selectors": list(getattr(payload, "selectors", ())),
                    "params": dict(getattr(payload, "params", {})),
                }

    def _log(self, message: str, **data: object) -> None:
        self.logger.info(message, **data)
