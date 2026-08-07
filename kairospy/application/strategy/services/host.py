from __future__ import annotations

from dataclasses import dataclass, replace
import asyncio

from ..domain.lifecycle import StrategyLifecycle
from ..domain.messages import EventEnvelope, LifecycleRecord
from ..protocol import ContextBus, EventStream, LifecycleJournal, SnapshotReader, Strategy
from .context import StrategyContext
from kairospy.strategy import StrategyLogger


@dataclass(frozen=True, slots=True)
class StrategyHostStatus:
    launch_id: str
    instance_id: str
    strategy_id: str
    state: StrategyLifecycle
    reason: str | None = None
    event_sequence: int = 0


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
        bus: ContextBus,
        snapshots: SnapshotReader,
        stream: EventStream,
        journal: LifecycleJournal,
        logger: StrategyLogger | None = None,
        snapshot_views: tuple[str, ...] = ("market.current",),
    ) -> None:
        if not launch_id.strip() or not instance_id.strip():
            raise ValueError("launch_id and instance_id are required")
        self.strategy = strategy
        self.launch_id = launch_id
        self.instance_id = instance_id
        self._bus = bus
        self._snapshots = snapshots
        self.stream = stream
        self.journal = journal
        self.logger = logger or StrategyLogger(fields={
            "launch_id": launch_id,
            "instance_id": instance_id,
            "strategy_id": strategy.strategy_id,
            "component": "strategy",
        })
        self.context = StrategyContext(
            strategy.strategy_id,
            instance_id=instance_id,
            bus=bus,
            snapshots=snapshots,
            request_observer=self._observe_request,
            logger=self.logger,
        )
        self.snapshot_views = snapshot_views
        self._status = StrategyHostStatus(launch_id, instance_id, strategy.strategy_id, StrategyLifecycle.CREATED)
        self._subscription_requests: set[str] = set()
        self._stop_requested = asyncio.Event()
        self._log("strategy host created")

    @property
    def status(self) -> StrategyHostStatus:
        return self._status

    def start(self) -> StrategyHostStatus:
        if self._status.state is not StrategyLifecycle.CREATED:
            raise RuntimeError(f"strategy can only start from created: {self._status.state}")
        self._transition(StrategyLifecycle.WAITING_FOR_DEPENDENCIES)
        self._log("strategy on_start begin")
        try:
            self._call("on_start", self.context._bind(None))
        except Exception as error:
            self._transition(StrategyLifecycle.FAILED, str(error))
            raise
        self._log(f"strategy on_start completed subscriptions={len(self._subscription_requests)}")
        if not self._refresh_dependencies():
            self._log(f"waiting for dependencies reason={self._status.reason}")
            return self._status
        try:
            if not self._bootstrap():
                self._log(f"waiting for snapshot reason={self._status.reason}")
                return self._status
        except Exception as error:
            self._transition(StrategyLifecycle.FAILED, str(error))
            raise
        self._transition(StrategyLifecycle.READY)
        return self._status

    def enable(self) -> StrategyHostStatus:
        if self._status.state is not StrategyLifecycle.READY:
            raise RuntimeError(f"strategy can only be enabled from ready: {self._status.state}")
        self._transition(StrategyLifecycle.RUNNING)
        return self._status

    def pause(self, reason: str = "paused by control") -> StrategyHostStatus:
        if self._status.state is not StrategyLifecycle.RUNNING:
            raise RuntimeError(f"strategy can only be paused from running: {self._status.state}")
        self._transition(StrategyLifecycle.PAUSED, reason)
        return self._status

    def resume(self) -> StrategyHostStatus:
        if self._status.state is not StrategyLifecycle.PAUSED:
            raise RuntimeError(f"strategy can only resume from paused: {self._status.state}")
        self._transition(StrategyLifecycle.RUNNING)
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
        self._transition(StrategyLifecycle.READY)
        return self._status

    def dispatch(self, event: EventEnvelope) -> None:
        if self._status.state is not StrategyLifecycle.RUNNING:
            return
        if event.stream_id != self.stream.stream_id:
            raise ValueError("event belongs to a different stream")
        if event.sequence != self._status.event_sequence + 1:
            error = RuntimeError("event stream is not continuous")
            self._transition(StrategyLifecycle.FAILED, str(error))
            raise error
        self.context._bind(event)
        hook = {
            "data": "on_data",
            "intent": "on_intent",
            "clock": "on_clock",
            "system": "on_system",
        }.get(event.domain, "on_data")
        event_time_source = (
            "none" if event.occurred_at is None
            else "market_event" if event.domain == "data"
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
        self._status = replace(self._status, event_sequence=event.sequence)

    async def run(self) -> None:
        """Consume the instance event stream after launch has enabled the strategy."""
        if self._status.state is not StrategyLifecycle.RUNNING:
            raise RuntimeError("strategy event loop requires a running strategy")
        self._stop_requested.clear()
        async for event in self.stream.events(after_sequence=self._status.event_sequence):
            if self._stop_requested.is_set():
                return
            self.dispatch(event)

    def stop(self) -> StrategyHostStatus:
        if self._status.state in {StrategyLifecycle.STOPPED, StrategyLifecycle.STOPPING}:
            return self._status
        self._stop_requested.set()
        self._transition(StrategyLifecycle.STOPPING)
        self._call("on_end", self.context._bind(None))
        self._transition(StrategyLifecycle.STOPPED)
        return self._status

    def _refresh_dependencies(self) -> bool:
        pending = [request_id for request_id in self._subscription_requests if self._bus.status(request_id).status not in {"ready", "accepted"}]
        if pending:
            self._status = replace(self._status, reason=f"dependencies pending: {', '.join(pending)}")
            return False
        else:
            self._status = replace(self._status, reason=None)
            return True

    def _bootstrap(self) -> bool:
        for view_key in self.snapshot_views:
            try:
                snapshot = self._snapshots.read(view_key)
            except (FileNotFoundError, KeyError):
                self._status = replace(self._status, reason=f"snapshot pending: {view_key}")
                return False
            if snapshot.event_stream_id != self.stream.stream_id:
                raise RuntimeError("snapshot event stream does not match strategy event stream")
            if not self.stream.can_join(snapshot.event_sequence):
                raise RuntimeError("snapshot watermark cannot be joined to event stream")
            self.context._install_snapshot(snapshot)
            self._status = replace(self._status, event_sequence=snapshot.event_sequence)
        return True

    def _call(self, name: str, *args: object) -> None:
        callback = getattr(self.strategy, name, None)
        if callback is None:
            return
        result = callback(*args)
        if result is not None:
            raise TypeError(f"{name} must return None; use context bus to interact with the system")

    def _transition(self, state: StrategyLifecycle, reason: str | None = None) -> None:
        self._status = StrategyHostStatus(self.launch_id, self.instance_id, self.strategy.strategy_id, state, reason, self._status.event_sequence)
        self.journal.append(LifecycleRecord(self.launch_id, self.instance_id, self.strategy.strategy_id, state.value, reason, self._status.event_sequence))
        self._log(
            f"strategy state={state.value} event_sequence={self._status.event_sequence}"
            + (f" reason={reason}" if reason else "")
        )

    def _observe_request(self, request: object, handle: object) -> None:
        if getattr(request, "operation", None) == "market.subscribe":
            request_id = getattr(handle, "request_id", None)
            if request_id:
                self._subscription_requests.add(request_id)
                self._log(f"market subscription requested request_id={request_id}")

    def _log(self, message: str, **data: object) -> None:
        self.logger.info(message, **data)
