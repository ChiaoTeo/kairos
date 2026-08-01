from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import asyncio
from typing import Callable, Mapping, Protocol, TypeVar


class ConnectionManager(Protocol):
    def register(self, key: str, resource: object, *, role: str = "resource") -> object:
        ...

    def resolve(self, key: str, *, role: str, factory: Callable[[], object | None]) -> object | None:
        ...

    def start(self) -> None:
        ...

    def stop(self) -> None:
        ...

    def reconnect(self, key: str | None = None) -> object | None:
        ...

    def health(self) -> Mapping[str, object]:
        ...


@dataclass(frozen=True, slots=True)
class NoopConnectionManager:
    def register(self, key: str, resource: object, *, role: str = "resource") -> object:
        _ = key, role
        return resource

    def resolve(self, key: str, *, role: str, factory: Callable[[], object | None]) -> object | None:
        _ = key, role
        return factory()

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def reconnect(self, key: str | None = None) -> object | None:
        _ = key
        return None

    def health(self) -> Mapping[str, object]:
        return {"status": "ready", "connections": 0}


T = TypeVar("T")


@dataclass(slots=True)
class ManagedConnection:
    key: str
    role: str
    resource: object
    factory: Callable[[], object | None] | None = None
    status: str = "registered"
    starts: int = 0
    reconnects: int = 0
    errors: int = 0
    last_started_at: datetime | None = None
    last_reconnected_at: datetime | None = None
    last_error: str | None = None

    def start(self) -> object:
        _call_optional(self.resource, "start")
        self.status = "ready"
        self.starts += 1
        self.last_started_at = _now()
        return self.resource

    def stop(self) -> None:
        try:
            _call_optional(self.resource, "stop")
            _call_optional(self.resource, "close")
        finally:
            self.status = "stopped"

    def reconnect(self) -> object:
        try:
            _call_optional(self.resource, "reconnect")
        except AttributeError:
            self.stop()
            replacement = None if self.factory is None else self.factory()
            if replacement is not None:
                self.resource = replacement
            self.start()
        else:
            self.status = "ready"
        self.reconnects += 1
        self.last_reconnected_at = _now()
        return self.resource

    def record_error(self, error: Exception) -> None:
        self.errors += 1
        self.status = "error"
        self.last_error = str(error)

    def health(self) -> Mapping[str, object]:
        payload: dict[str, object] = {
            "key": self.key,
            "role": self.role,
            "status": self.status,
            "starts": self.starts,
            "reconnects": self.reconnects,
            "errors": self.errors,
            "last_started_at": None if self.last_started_at is None else self.last_started_at.isoformat(),
            "last_reconnected_at": None if self.last_reconnected_at is None else self.last_reconnected_at.isoformat(),
            "last_error": self.last_error,
        }
        resource_health = getattr(self.resource, "health", None)
        if callable(resource_health):
            try:
                value = resource_health()
            except Exception as error:
                self.record_error(error)
                payload["status"] = self.status
                payload["last_error"] = self.last_error
            else:
                if isinstance(value, Mapping):
                    payload["resource"] = dict(value)
        return payload


class DefaultConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, ManagedConnection] = {}
        self._started = False

    def register(self, key: str, resource: T, *, role: str = "resource") -> T:
        normalized = _key(key)
        existing = self._connections.get(normalized)
        if existing is not None:
            return existing.resource  # type: ignore[return-value]
        connection = ManagedConnection(normalized, role, resource)
        self._connections[normalized] = connection
        if self._started:
            connection.start()
        return resource

    def resolve(self, key: str, *, role: str, factory: Callable[[], T | None]) -> T | None:
        normalized = _key(key)
        existing = self._connections.get(normalized)
        if existing is not None:
            return existing.resource  # type: ignore[return-value]
        resource = factory()
        if resource is None:
            return None
        connection = ManagedConnection(normalized, role, resource, factory=factory)
        self._connections[normalized] = connection
        if self._started:
            connection.start()
        return resource

    def start(self) -> None:
        self._started = True
        for connection in self._connections.values():
            if connection.status != "ready":
                connection.start()

    def stop(self) -> None:
        for connection in reversed(tuple(self._connections.values())):
            connection.stop()
        self._started = False

    def reconnect(self, key: str | None = None) -> object | None:
        if key is not None:
            return self._connections[_key(key)].reconnect()
        for connection in self._connections.values():
            connection.reconnect()
        return None

    def record_error(self, key: str, error: Exception) -> None:
        connection = self._connections.get(_key(key))
        if connection is not None:
            connection.record_error(error)

    def health(self) -> Mapping[str, object]:
        connections = tuple(connection.health() for connection in self._connections.values())
        status = "ready"
        if any(item.get("status") == "error" for item in connections):
            status = "degraded"
        elif not self._started:
            status = "stopped" if connections else "ready"
        return {
            "status": status,
            "connections": len(connections),
            "items": connections,
        }


def _key(value: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError("connection key is required")
    return text


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _call_optional(resource: object, method: str) -> None:
    callback = getattr(resource, method, None)
    if not callable(callback):
        if method == "reconnect":
            raise AttributeError(method)
        return
    result = callback()
    if asyncio.iscoroutine(result):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(result)
        else:
            loop.create_task(result)


__all__ = ["ConnectionManager", "DefaultConnectionManager", "ManagedConnection", "NoopConnectionManager"]
