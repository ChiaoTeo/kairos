from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from kairospy.infrastructure.integrations.application.lifecycle import (
    HealthCheckable,
    ManagedConnection,
    ManagedResource,
    Reconnectable,
    Startable,
    Stoppable,
)
from kairospy.infrastructure.integrations.domain import (
    AccessScope,
    CredentialRef,
    ConnectionHealth,
    ConnectionIdentity,
    ConnectionState,
    IntegrationBinding,
    IntegrationCapability,
    IntegrationRoute,
    ProductFamily,
    TransportKind,
)


class RuntimeMode(StrEnum):
    LIVE = "live"
    PAPER = "paper"
    BACKTEST = "backtest"


@dataclass(frozen=True, slots=True)
class IntegrationConnectionSpec:
    """Specification for exactly one transport link.

    A connection never combines market, account, and execution routes. It
    owns one route, one access scope, one transport, and one capability. A
    route may name the exchange, broker, and provider involved in selecting
    the link; the concrete connection still exposes only its selected
    capability.
    """

    connection_id: str
    route: IntegrationRoute
    product: ProductFamily | None
    access: AccessScope
    transport: TransportKind
    credential: CredentialRef | None = None
    mode: RuntimeMode = RuntimeMode.LIVE
    capability: IntegrationCapability | None = None

    def __post_init__(self) -> None:
        if not self.connection_id.strip():
            raise ValueError("integration connection_id is required")
        if self.access is AccessScope.PRIVATE and self.credential is None and self.mode is RuntimeMode.LIVE:
            raise ValueError("live private connections require a credential")
        if self.capability is None:
            capability = {
                TransportKind.MARKET_STREAM: IntegrationCapability.MARKET_STREAM,
                TransportKind.USER_STREAM: IntegrationCapability.ACCOUNT_STREAM,
                TransportKind.REST: (
                    IntegrationCapability.ACCOUNT_READ
                    if self.access is AccessScope.PRIVATE
                    else IntegrationCapability.MARKET_DATA
                ),
                TransportKind.REQUEST_API: IntegrationCapability.ORDER_ENTRY,
            }[self.transport]
            object.__setattr__(self, "capability", capability)
        for participant in self.route.participants:
            IntegrationBinding(
                participant=participant,
                product=self.product,
                access=self.access,
                transport=self.transport,
            )

    @property
    def binding(self) -> IntegrationBinding:
        return IntegrationBinding(
            participant=self.route.participants[0],
            product=self.product,
            access=self.access,
            transport=self.transport,
        )

    @property
    def bindings(self) -> tuple[IntegrationBinding, ...]:
        return tuple(
            IntegrationBinding(
                participant=participant,
                product=self.product,
                access=self.access,
                transport=self.transport,
            )
            for participant in self.route.participants
        )

    @property
    def participants(self):
        return self.route.participants


class IntegrationConnection(ManagedConnection, Protocol):
    @property
    def identity(self) -> ConnectionIdentity: ...

    @property
    def state(self) -> ConnectionState: ...

    @property
    def access(self) -> AccessScope: ...

    @property
    def transport(self) -> TransportKind: ...


class IntegrationConnectionApplication(Protocol):
    def connect(self, spec: IntegrationConnectionSpec) -> IntegrationConnection: ...


__all__ = [
    "IntegrationConnection",
    "IntegrationConnectionApplication",
    "IntegrationConnectionSpec",
    "RuntimeMode",
    "Startable",
    "Stoppable",
    "Reconnectable",
    "ManagedResource",
    "HealthCheckable",
    "ManagedConnection",
]
