from __future__ import annotations

from datetime import datetime, timezone

from kairospy.application.actor.support.base import BusinessActor
from kairospy.application.support.messaging import Message, MessageBus
from .commands import (
    AssessRiskCommand,
    ConfigureRiskBudgetsCommand,
    ConsumeRiskCommand,
    ReleaseRiskCommand,
    ReserveRiskCommand,
)


class RiskActor(BusinessActor):
    """Own risk usecase commands and future risk decisions."""

    def __init__(self, application: object | None = None, *, bus: MessageBus | None = None, projectors: object | None = None) -> None:
        super().__init__("risk", bus=bus)
        self.application = application
        self.projectors = projectors

    async def dispatch_command(self, command: object, *, correlation_id: str | None = None, causation_id: str | None = None) -> object:
        now = datetime.now(timezone.utc)
        request = getattr(command, "request", None)
        if request is not None:
            candidate = getattr(request, "assessment", request)
            request_at = getattr(candidate, "at", None)
            if isinstance(request_at, datetime):
                now = request_at
        correlation = correlation_id or _command_correlation(command)
        sequence = self._next_sequence()
        return await self.ask(Message("risk.command", command, now, "risk.actor", sequence, message_id=f"risk-command-{sequence}", correlation_id=correlation, causation_id=causation_id))

    def apply_command(self, command: object) -> object:
        application = self.application
        if application is None:
            raise RuntimeError("risk actor has no risk application")
        if isinstance(command, ConfigureRiskBudgetsCommand):
            return application.configure(command.budgets)
        if isinstance(command, AssessRiskCommand):
            return application.assess(command.request)
        if isinstance(command, ReserveRiskCommand):
            return application.reserve(command.request)
        if isinstance(command, ReleaseRiskCommand):
            return application.release(command.reservation_id)
        if isinstance(command, ConsumeRiskCommand):
            return application.consume(command.reservation_id)
        raise TypeError(f"unsupported risk command: {type(command).__name__}")

    def _next_sequence(self) -> int:
        sequence = getattr(self, "_command_sequence", 0) + 1
        self._command_sequence = sequence
        return sequence

    async def process(self, message: Message) -> object:
        result: object = None
        if message.topic == "risk.command":
            result = self.apply_command(message.payload)
            if self.bus is not None and isinstance(
                message.payload,
                (ReserveRiskCommand, ReleaseRiskCommand, ConsumeRiskCommand),
            ):
                await self.bus.publish(
                    Message(
                        "risk.reservation.updated",
                        result,
                        message.time,
                        "risk.actor",
                        self._next_sequence(),
                        correlation_id=message.correlation_id,
                        causation_id=message.message_id,
                    )
                )
        projector_event = getattr(self.projectors, "on_event", None)
        if callable(projector_event):
            projector_event(message)
        return result


__all__ = ["RiskActor"]


def _command_correlation(command: object) -> str | None:
    request = getattr(command, "request", None)
    reservation_id = getattr(request, "reservation_id", None)
    if reservation_id:
        return str(reservation_id)
    candidate = getattr(request, "assessment", request)
    request_id = getattr(candidate, "request_id", None)
    if request_id:
        return str(request_id)
    reservation_id = getattr(command, "reservation_id", None)
    return None if reservation_id is None else str(reservation_id)
