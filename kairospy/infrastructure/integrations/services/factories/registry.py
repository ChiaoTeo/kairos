from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from kairospy.infrastructure.integrations.application.connections import (
    IntegrationConnection,
    IntegrationConnectionSpec,
)
from kairospy.infrastructure.integrations.domain import (
    BrokerId,
    ExchangeId,
    ParticipantKind,
    ParticipantRef,
    ProductFamily,
    ProviderId,
)
from kairospy.infrastructure.integrations.services.connection_services.binance_spot import (
    BinanceSpotConnectionService,
    MassiveMarketConnectionService,
)
from kairospy.infrastructure.integrations.services.connection_services.binance_equity import BinanceEquityConnectionService


ConnectionServiceFactory = Callable[[IntegrationConnectionSpec], IntegrationConnection]


@dataclass(slots=True)
class ConnectionServiceRegistry:
    _factories: dict[tuple[tuple[ParticipantRef, ...], ProductFamily | None], ConnectionServiceFactory] = field(default_factory=dict)

    @classmethod
    def with_builtins(cls) -> "ConnectionServiceRegistry":
        registry = cls()
        registry.register(
            participants=(
                ParticipantRef(ParticipantKind.EXCHANGE, ExchangeId.BINANCE),
                ParticipantRef(ParticipantKind.BROKER, BrokerId.BINANCE),
            ),
            product=ProductFamily.SPOT,
            factory=BinanceSpotConnectionService,
        )
        registry.register(
            participants=(ParticipantRef(ParticipantKind.EXCHANGE, ExchangeId.BINANCE),),
            product=ProductFamily.SPOT,
            factory=BinanceSpotConnectionService,
        )
        registry.register(
            participants=(ParticipantRef(ParticipantKind.EXCHANGE, ExchangeId.BINANCE),),
            product=ProductFamily.EQUITY,
            factory=BinanceEquityConnectionService,
        )
        registry.register(
            participants=(ParticipantRef(ParticipantKind.BROKER, BrokerId.BINANCE),),
            product=ProductFamily.SPOT,
            factory=BinanceSpotConnectionService,
        )
        registry.register(
            participants=(
                ParticipantRef(ParticipantKind.PROVIDER, ProviderId.MASSIVE),
                ParticipantRef(ParticipantKind.BROKER, BrokerId.BINANCE),
            ),
            product=ProductFamily.SPOT,
            factory=BinanceSpotConnectionService,
        )
        registry.register(
            participants=(
                ParticipantRef(ParticipantKind.PROVIDER, ProviderId.MASSIVE),
                ParticipantRef(ParticipantKind.BROKER, BrokerId.BINANCE),
                ParticipantRef(ParticipantKind.EXCHANGE, ExchangeId.BINANCE),
            ),
            product=ProductFamily.SPOT,
            factory=BinanceSpotConnectionService,
        )
        registry.register(
            participants=(ParticipantRef(ParticipantKind.PROVIDER, ProviderId.MASSIVE),),
            product=None,
            factory=MassiveMarketConnectionService,
        )
        return registry

    def register(
        self,
        *,
        participants: tuple[ParticipantRef, ...],
        product: ProductFamily | None,
        factory: ConnectionServiceFactory,
    ) -> None:
        key = (_canonical_participants(participants), product)
        if key in self._factories:
            raise ValueError(f"connection service already registered: {key!r}")
        self._factories[key] = factory

    def create(self, spec: IntegrationConnectionSpec) -> IntegrationConnection:
        key = ((spec.participant,), spec.product)
        factory = self._factories.get(key)
        if factory is None:
            raise LookupError(f"no connection service for participants={participants!r}, product={spec.product!r}")
        return factory(spec)


def _canonical_participants(participants: tuple[ParticipantRef, ...]) -> tuple[ParticipantRef, ...]:
    return tuple(sorted(set(participants), key=lambda item: (item.kind.value, item.id.value)))


__all__ = ["ConnectionServiceFactory", "ConnectionServiceRegistry"]
