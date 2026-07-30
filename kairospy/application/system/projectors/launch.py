from __future__ import annotations

from datetime import timedelta

from kairospy.application.runtime.orchestration.state import RuntimeStep
from kairospy.application.system.artifacts.output import LaunchOutput
from kairospy.application.system.projectors.account import AccountCurrentProjector
from kairospy.application.system.projectors.timeline import TimelineProjector
from kairospy.core.views import ViewStore


class LaunchArtifactProjector:
    def __init__(self, output: LaunchOutput, *, timeline_sample_interval: str | timedelta | None = "1m") -> None:
        self.account_current = AccountCurrentProjector(output)
        self.timeline = TimelineProjector(output, sample_interval=timeline_sample_interval)

    def publish_started(self, views: ViewStore) -> None:
        self.account_current.publish_views(views)
        self.timeline.publish_views(views)

    def publish_step(self, step: RuntimeStep, views: ViewStore) -> None:
        step_views = step.views or views
        if step.kind == "event" and step.event is not None:
            self.timeline.on_event(step.event)
        elif step.kind == "decision":
            self.timeline.on_intents(step.intents, step.context, step.hook)
        self.account_current.publish_views(step_views, as_of=step.as_of)
        self.timeline.publish_views(step_views, as_of=step.as_of)


__all__ = ["LaunchArtifactProjector"]
