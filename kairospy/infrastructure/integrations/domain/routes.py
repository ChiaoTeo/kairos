from __future__ import annotations

from dataclasses import dataclass

from kairospy.domain.reference import BrokerRef, ExchangeRef, ParticipantRef, ProviderRef


@dataclass(frozen=True, slots=True)
class IntegrationRoute:
    """Business participants used by the Gateway registry for resolution."""

    exchange: ExchangeRef | None = None
    broker: BrokerRef | None = None
    provider: ProviderRef | None = None

    def __post_init__(self) -> None:
        if self.exchange is None and self.broker is None and self.provider is None:
            raise ValueError("integration route requires an exchange, broker, or provider")

    @property
    def participants(self) -> tuple[ParticipantRef, ...]:
        values = []
        if self.exchange is not None:
            values.append(self.exchange.participant)
        if self.broker is not None:
            values.append(self.broker.participant)
        if self.provider is not None:
            values.append(self.provider.participant)
        return tuple(values)


__all__ = ["IntegrationRoute"]
