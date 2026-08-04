from kairospy.application.actor.support.base import BusinessActor


class RiskActor(BusinessActor):
    """Own risk usecase commands and future risk decisions."""

    def __init__(self, application: object | None = None, *, projectors: object | None = None) -> None:
        super().__init__("risk")
        self.application = application
        self.projectors = projectors

    async def process(self, message: object) -> None:
        projector_event = getattr(self.projectors, "on_event", None)
        if callable(projector_event):
            projector_event(message)


__all__ = ["RiskActor"]
