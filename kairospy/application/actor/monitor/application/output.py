from __future__ import annotations

from datetime import datetime, timedelta

from kairospy.application.support.launch.application.artifacts import LaunchOutput
from kairospy.application.support.runtime.application.views import ViewStore
from .timeline import TimelineProjector


class AccountCurrentOutput:
    def __init__(self, output: LaunchOutput) -> None:
        self.output = output
        self._last_written: dict[str, object] = {}

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        for key in tuple(name for name in views.envelopes() if name.startswith("account.current.")):
            account_view = views.get(key)
            if account_view is None:
                continue
            marker = (
                as_of,
                getattr(account_view, "event_count", None),
                getattr(account_view, "last_event_time", None),
                getattr(account_view, "equity", None),
                getattr(account_view, "cash", None),
                getattr(account_view, "net_profit", None),
                getattr(account_view, "total_return", None),
                len(tuple(getattr(account_view, "positions", ()) or ())),
                len(tuple(getattr(account_view, "open_orders", ()) or ())),
                len(tuple(getattr(account_view, "pending_orders", ()) or ())),
            )
            if self._last_written.get(key) == marker:
                continue
            self.output.update_current(
                "account",
                {
                    "account_view": account_view,
                    "equity": getattr(account_view, "equity", None),
                    "net_profit": getattr(account_view, "net_profit", None),
                    "total_return": getattr(account_view, "total_return", None),
                },
            )
            self._last_written[key] = marker


class MonitorCurrentOutput:
    """Persist Monitor-owned health snapshots as the current system artifact."""

    def __init__(self, output: LaunchOutput) -> None:
        self.output = output
        self._last_marker: object | None = None

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        health = views.get("system.health")
        freshness = views.get("system.freshness")
        operations = views.get("system.operations")
        alerts = views.get("system.alerts")
        marker = (as_of, repr(health), repr(freshness), repr(operations), repr(alerts))
        if marker == self._last_marker:
            return
        self.output.update_current(
            "system",
            {
                "health": health,
                "freshness": freshness,
                "operations": operations,
                "alerts": alerts,
            },
        )
        self._last_marker = marker


class MonitorOutput:
    """Monitor-owned launch and timeline output."""

    def __init__(self, output: LaunchOutput, *, timeline_sample_interval: str | timedelta | None = "1m", monitor_actor: object | None = None) -> None:
        self.output = output
        self.monitor_actor = monitor_actor
        self.account_current = AccountCurrentOutput(output)
        self.system_current = MonitorCurrentOutput(output)
        self.timeline = TimelineProjector(output, sample_interval=timeline_sample_interval)

    def publish_started(self, views: ViewStore) -> None:
        self.account_current.publish_views(views)
        self.system_current.publish_views(views)
        self.timeline.publish_views(views)

    def publish_cycle(self, cycle: object, views: ViewStore) -> None:
        step_views = getattr(cycle, "views", None) or views
        event = getattr(cycle, "event", None)
        if event is not None:
            self.timeline.on_event(event)
        if getattr(cycle, "dispatched", False):
            result = getattr(cycle, "output", None)
            self.timeline.on_intents(
                tuple(getattr(result, "intents", ())),
                getattr(result, "context", None),
                getattr(result, "hook", getattr(cycle, "hook", "")),
            )
        as_of = getattr(cycle, "as_of", None)
        self.account_current.publish_views(step_views, as_of=as_of)
        self.system_current.publish_views(step_views, as_of=as_of)
        self.timeline.publish_views(step_views, as_of=as_of)

    def publish_connection_health(self, health: object) -> None:
        observe = getattr(self.monitor_actor, "record_connection_health", None)
        if callable(observe):
            observe(health)
        payload = health if isinstance(health, dict) else {"status": "unknown", "details": health}
        self.output.update_current("system", {"connections": payload})


class MonitorProjectionPipeline:
    """Collect view updates from the business actors for the monitor."""

    def __init__(self, *, views: ViewStore, actors: tuple[object, ...]) -> None:
        self.views = views
        self.actors = actors
        for actor in actors:
            projectors = getattr(actor, "projectors", None)
            register = getattr(projectors, "register_views", None)
            if callable(register):
                register(views)

    def publish_for_event(self, event: object) -> None:
        for actor in self.actors:
            projectors = getattr(actor, "projectors", None)
            publish = getattr(projectors, "publish_views", None)
            if callable(publish):
                publish(self.views, as_of=getattr(event, "time", None))

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        if not intents:
            return
        for actor in self.actors:
            projectors = getattr(actor, "projectors", None)
            on_intents = getattr(projectors, "on_intents", None)
            if callable(on_intents):
                on_intents(intents, context, hook)
        for actor in self.actors:
            projectors = getattr(actor, "projectors", None)
            publish = getattr(projectors, "publish_views", None)
            if callable(publish):
                publish(self.views, as_of=getattr(context, "now", None))


class MonitorOutputCoordinator:
    """Bridge actor-owned views and monitor-owned launch output."""

    def __init__(self, *, actors: tuple[object, ...], monitor_output: MonitorOutput) -> None:
        self.pipeline: MonitorProjectionPipeline | None = None
        self.actors = actors
        self.monitor_output = monitor_output

    def attach(self, *, views: ViewStore) -> None:
        self.pipeline = MonitorProjectionPipeline(views=views, actors=self.actors)

    def publish_for_event(self, event: object) -> None:
        if self.pipeline is not None:
            self.pipeline.publish_for_event(event)

    def publish_started(self, views: ViewStore) -> None:
        self.monitor_output.publish_started(views)

    def publish_connection_health(self, health: object) -> None:
        self.monitor_output.publish_connection_health(health)

    def publish_cycle(self, cycle: object, views: ViewStore) -> None:
        self.monitor_output.publish_cycle(cycle, views)

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        observe = getattr(self.monitor_output, "monitor_actor", None)
        record = getattr(observe, "record_intents", None)
        if callable(record):
            record(intents, context, hook)
        if self.pipeline is not None:
            self.pipeline.on_intents(intents, context, hook)


__all__ = [
    "AccountCurrentOutput",
    "MonitorOutput",
    "MonitorOutputCoordinator",
    "MonitorCurrentOutput",
    "MonitorProjectionPipeline",
]
