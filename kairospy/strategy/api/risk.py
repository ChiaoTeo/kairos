"""Strategy re-exports of the Risk-owned event contract."""

from kairospy.contracts.risk.events import (
    RiskCircuitClosedEvent,
    RiskCircuitOpenedEvent,
    RiskDecisionMadeEvent,
    RiskEventVariant,
    RiskReservationConsumedEvent,
    RiskReservationExpiredEvent,
    RiskReservationReleasedEvent,
    RiskReservationReservedEvent,
)

RiskEvent = RiskEventVariant

__all__ = [
    "RiskCircuitClosedEvent",
    "RiskCircuitOpenedEvent",
    "RiskDecisionMadeEvent",
    "RiskEvent",
    "RiskEventVariant",
    "RiskReservationConsumedEvent",
    "RiskReservationExpiredEvent",
    "RiskReservationReleasedEvent",
    "RiskReservationReservedEvent",
]
