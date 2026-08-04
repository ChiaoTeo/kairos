"""Risk Actor-owned projections."""

from kairospy.application.usecases.risk.application.projector import RiskProjector


class RiskActorProjectors:
    def __init__(self, application: object | None = None) -> None:
        self.risk = RiskProjector(application)  # type: ignore[arg-type]

    def on_event(self, event: object) -> None:
        self.risk.on_event(event)  # type: ignore[arg-type]

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        return None

    def register_views(self, views: object) -> None:
        self.risk.register_views(views)  # type: ignore[arg-type]

    def publish_views(self, views: object, *, as_of: object | None = None) -> None:
        self.risk.publish_views(views, as_of=as_of)  # type: ignore[arg-type]


__all__ = ["RiskActorProjectors"]
