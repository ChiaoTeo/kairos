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
    ParticipantRef,
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

    A connection never combines market, account, and execution routes.  It
    owns one participant, one access scope, and one transport.  A concrete
    connection may implement several business Protocols when the link
    legitimately serves them (for example account reads and order entry over
    one private REST client).
    """

    connection_id: str
    participant: ParticipantRef
    product: ProductFamily | None
    access: AccessScope
    transport: TransportKind
    credential: CredentialRef | None = None
    mode: RuntimeMode = RuntimeMode.LIVE

    def __post_init__(self) -> None:
        if not self.connection_id.strip():
            raise ValueError("integration connection_id is required")
        if self.access is AccessScope.PRIVATE and self.credential is None and self.mode is RuntimeMode.LIVE:
            raise ValueError("live private connections require a credential")
        IntegrationBinding(
            participant=self.participant,
            product=self.product,
            access=self.access,
            transport=self.transport,
        )

    @property
    def binding(self) -> IntegrationBinding:
        return IntegrationBinding(
            participant=self.participant,
            product=self.product,
            access=self.access,
            transport=self.transport,
        )


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
