from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .bindings import AccessScope, IntegrationBinding, TransportKind
from .capabilities import IntegrationCapability
from .routes import IntegrationRoute
from kairospy.domain.reference import ParticipantRef
from .products import ProductFamily


class ConnectionLifecycle(StrEnum):
    CREATED = "created"
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ConnectionIdentity:
    connection_id: str
    route: IntegrationRoute
    product: ProductFamily | None
    access: AccessScope
    transport: TransportKind
    capability: IntegrationCapability

    def __post_init__(self) -> None:
        if not self.connection_id.strip():
            raise ValueError("connection id is required")
        if not self.route.participants:
            raise ValueError("connection requires at least one route participant")

    @property
    def participants(self) -> tuple[ParticipantRef, ...]:
        return self.route.participants


@dataclass(frozen=True, slots=True)
class ConnectionState:
    identity: ConnectionIdentity
    lifecycle: ConnectionLifecycle = ConnectionLifecycle.CREATED
    bindings: tuple[IntegrationBinding, ...] = ()
    authenticated: bool = False
    connected_at: datetime | None = None
    last_error: str | None = None
    reconnect_count: int = 0

    def __post_init__(self) -> None:
        if self.connected_at is not None and self.connected_at.tzinfo is None:
            raise ValueError("connection connected_at must be timezone-aware")
        if self.reconnect_count < 0:
            raise ValueError("connection reconnect_count cannot be negative")
        participants = set(self.identity.participants)
        if any(binding.participant not in participants for binding in self.bindings):
            raise ValueError("connection binding participant is not in connection identity")
        if self.identity.product is not None and any(
            binding.product is not None and binding.product is not self.identity.product
            for binding in self.bindings
        ):
            raise ValueError("connection binding product does not match connection identity")


@dataclass(frozen=True, slots=True)
class ConnectionHealth:
    lifecycle: ConnectionLifecycle
    healthy: bool
    authenticated: bool
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class RemoteSubscriptionSnapshot:
    connection_id: str
    subscription_ids: tuple[str, ...]
    observed_at: datetime
    authoritative: bool = True
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.connection_id.strip():
            raise ValueError("subscription snapshot connection_id is required")
        if self.observed_at.tzinfo is None:
            raise ValueError("subscription snapshot timestamp must be timezone-aware")
        if any(not subscription_id.strip() for subscription_id in self.subscription_ids):
            raise ValueError("subscription ids cannot be blank")


__all__ = [
    "ConnectionHealth",
    "ConnectionIdentity",
    "ConnectionLifecycle",
    "ConnectionState",
    "RemoteSubscriptionSnapshot",
]
