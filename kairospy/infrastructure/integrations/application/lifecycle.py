from __future__ import annotations

from typing import Protocol

from kairospy.infrastructure.integrations.domain import (
    ConnectionHealth,
    ConnectionIdentity,
    ConnectionState,
)


class Startable(Protocol):
    async def start(self) -> None: ...


class Stoppable(Protocol):
    async def stop(self) -> None: ...


class Reconnectable(Protocol):
    async def reconnect(self) -> None: ...


class ManagedResource(Startable, Stoppable, Reconnectable, Protocol):
    """Lifecycle contract shared by connection-owned resources."""


class HealthCheckable(Protocol):
    def health(self) -> ConnectionHealth: ...


class ManagedConnection(ManagedResource, HealthCheckable, Protocol):
    """Complete lifecycle contract for an assembled integration connection."""


class IntegrationConnection(ManagedConnection, Protocol):
    @property
    def identity(self) -> ConnectionIdentity: ...

    @property
    def state(self) -> ConnectionState: ...


__all__ = [
    "Startable",
    "Stoppable",
    "Reconnectable",
    "ManagedResource",
    "HealthCheckable",
    "ManagedConnection",
    "IntegrationConnection",
]
