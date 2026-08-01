from __future__ import annotations

from datetime import timedelta

from kairospy.application.support.system.artifacts.output import LaunchOutput
from kairospy.application.support.system.projectors.account import AccountCurrentProjector
from kairospy.application.support.system.projectors.timeline import TimelineProjector
from kairospy.core.views import ViewStore


class LaunchArtifactProjector:
    def __init__(self, output: LaunchOutput, *, timeline_sample_interval: str | timedelta | None = "1m") -> None:
        self.output = output
        self.account_current = AccountCurrentProjector(output)
        self.timeline = TimelineProjector(output, sample_interval=timeline_sample_interval)

    def publish_started(self, views: ViewStore) -> None:
        self.account_current.publish_views(views)
        self.timeline.publish_views(views)

    def publish_step(self, step: object, views: ViewStore) -> None:
        step_views = getattr(step, "views", None) or views
        kind = getattr(step, "kind", "")
        event = getattr(step, "event", None)
        if kind == "event" and event is not None:
            self.timeline.on_event(event)
        elif kind == "decision":
            self.timeline.on_intents(getattr(step, "intents", ()), getattr(step, "context", None), getattr(step, "hook", ""))
        as_of = getattr(step, "as_of", None)
        self.account_current.publish_views(step_views, as_of=as_of)
        self.timeline.publish_views(step_views, as_of=as_of)

    def publish_connection_health(self, health: object) -> None:
        if isinstance(health, dict):
            payload = health
        else:
            payload = {"status": "unknown", "details": health}
        self.output.update_current("system", {"connections": payload})


__all__ = ["LaunchArtifactProjector"]
