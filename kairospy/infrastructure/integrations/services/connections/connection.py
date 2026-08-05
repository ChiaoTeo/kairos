from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from kairospy.infrastructure.integrations.application.connections import (
    IntegrationConnection,
    IntegrationConnectionSpec,
    ManagedResource,
)
from kairospy.infrastructure.integrations.domain import (
    AccessScope,
    ConnectionHealth,
    ConnectionIdentity,
    ConnectionLifecycle,
    ConnectionState,
    TransportKind,
)


class ConnectionComponent(ManagedResource, Protocol):
    """A lifecycle-managed resource owned by one physical link."""


@dataclass(slots=True)
class Connection(IntegrationConnection):
    """Base implementation for one transport link."""

    spec: IntegrationConnectionSpec
    components: tuple[ConnectionComponent, ...] = ()
    _state: ConnectionState = field(init=False)

    def __post_init__(self) -> None:
        identity = ConnectionIdentity(
            connection_id=self.spec.connection_id,
            route=self.spec.route,
            product=self.spec.product,
            asset_type=self.spec.asset_type,
            access=self.spec.access,
            transport=self.spec.transport,
            capability=self.spec.capability,
        )
        self._state = ConnectionState(identity=identity, bindings=self.spec.bindings)

    @property
    def identity(self) -> ConnectionIdentity:
        return self._state.identity

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def access(self) -> AccessScope:
        return self.spec.access

    @property
    def transport(self) -> TransportKind:
        return self.spec.transport

    async def start(self) -> None:
        if self._state.lifecycle is ConnectionLifecycle.READY:
            return
        self._state = _transition(self._state, ConnectionLifecycle.STARTING)
        started: list[ConnectionComponent] = []
        try:
            for component in self.components:
                await component.start()
                started.append(component)
        except Exception as error:
            for component in reversed(started):
                try:
                    await component.stop()
                except Exception:
                    pass
            self._state = _failed(self._state, error)
            raise
        self._state = _transition(
            self._state,
            ConnectionLifecycle.READY,
            authenticated=self.spec.access is AccessScope.PRIVATE and self.spec.credential is not None,
            connected_at=datetime.now(timezone.utc),
        )

    async def stop(self) -> None:
        if self._state.lifecycle in {ConnectionLifecycle.STOPPED, ConnectionLifecycle.CREATED}:
            self._state = _transition(self._state, ConnectionLifecycle.STOPPED)
            return
        self._state = _transition(self._state, ConnectionLifecycle.STOPPING)
        try:
            for component in reversed(self.components):
                await component.stop()
        except Exception as error:
            self._state = _failed(self._state, error)
            raise
        self._state = _transition(self._state, ConnectionLifecycle.STOPPED)

    async def reconnect(self) -> None:
        self._state = _transition(self._state, ConnectionLifecycle.STARTING)
        try:
            for component in self.components:
                await component.reconnect()
        except Exception as error:
            self._state = _failed(self._state, error)
            raise
        self._state = _transition(
            self._state,
            ConnectionLifecycle.READY,
            authenticated=self.spec.access is AccessScope.PRIVATE and self.spec.credential is not None,
            connected_at=datetime.now(timezone.utc),
            reconnect_count=self._state.reconnect_count + 1,
        )

    def health(self) -> ConnectionHealth:
        lifecycle = self._state.lifecycle
        return ConnectionHealth(
            lifecycle=lifecycle,
            healthy=lifecycle in {ConnectionLifecycle.READY, ConnectionLifecycle.DEGRADED},
            authenticated=self._state.authenticated,
            last_error=self._state.last_error,
        )


@dataclass(slots=True)
class CompositeConnectionComponent:
    name: str
    children: tuple[ConnectionComponent, ...] = ()

    async def start(self) -> None:
        for child in self.children:
            await child.start()

    async def stop(self) -> None:
        for child in reversed(self.children):
            await child.stop()

    async def reconnect(self) -> None:
        for child in self.children:
            await child.reconnect()


def _transition(state: ConnectionState, lifecycle: ConnectionLifecycle, **changes: object) -> ConnectionState:
    values = {
        "identity": state.identity,
        "lifecycle": lifecycle,
        "bindings": state.bindings,
        "authenticated": state.authenticated,
        "connected_at": state.connected_at,
        "last_error": state.last_error,
        "reconnect_count": state.reconnect_count,
    }
    values.update(changes)
    return ConnectionState(**values)


def _failed(state: ConnectionState, error: Exception) -> ConnectionState:
    return _transition(state, ConnectionLifecycle.FAILED, last_error=str(error))


__all__ = ["CompositeConnectionComponent", "ConnectionComponent", "Connection"]
