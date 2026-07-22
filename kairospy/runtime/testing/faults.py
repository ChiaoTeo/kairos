from __future__ import annotations

from enum import StrEnum
from typing import Mapping, Protocol


class RuntimeFaultPoint(StrEnum):
    AFTER_ORDER_SUBMITTING_BEFORE_VENUE = "after_order_submitting_before_venue"
    AFTER_VENUE_ACCEPT_BEFORE_ACK_PERSIST = "after_venue_accept_before_ack_persist"
    DURING_EXECUTION_TRANSACTION = "during_execution_transaction"
    DURING_LEDGER_EVENT_TRANSACTION = "during_ledger_event_transaction"


class RuntimeFaultInjector(Protocol):
    def checkpoint(self, point: RuntimeFaultPoint, context: Mapping[str, object]) -> None: ...


class InjectedRuntimeFailure(RuntimeError):
    pass


class OneShotRuntimeFaultInjector:
    """Deterministic drill injector; normal runtime composition supplies no injector."""

    def __init__(self, point: RuntimeFaultPoint) -> None:
        self.point = point
        self.triggered = False

    def checkpoint(self, point: RuntimeFaultPoint, context: Mapping[str, object]) -> None:
        if point is self.point and not self.triggered:
            self.triggered = True
            raise InjectedRuntimeFailure(f"injected runtime failure: {point.value}")


def inject(injector: RuntimeFaultInjector | None, point: RuntimeFaultPoint, **context: object) -> None:
    if injector is not None:
        injector.checkpoint(point, context)
