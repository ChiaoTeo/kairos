"""Actor-owned integration connection scope and lifecycle state."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _key(value: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError("connection key is required")
    return text


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


@dataclass(frozen=True, slots=True)
class NoopConnectionManager:
    def register(self, key: str, resource: object, *, role: str = "resource") -> object:
        return resource

    def resolve(self, key: str, *, role: str, factory: object) -> object | None:
        return factory.create_connection()

    def get(self, key: str) -> object | None: return None
    def remove(self, key: str) -> object | None: return None
    def start(self) -> None: return None
    def stop(self) -> None: return None
    def start_roles(self, roles: tuple[str, ...]) -> None: return None
    def stop_roles(self, roles: tuple[str, ...]) -> None: return None
    def reconnect(self, key: str | None = None) -> object | None: return None
    def health(self) -> Mapping[str, object]: return {"status": "ready", "connections": 0}


@dataclass(slots=True)
class ScopedConnection:
    key: str
    role: str
    resource: object
    factory: object | None = None
    status: str = "registered"
    starts: int = 0
    reconnects: int = 0
    errors: int = 0
    last_started_at: datetime | None = None
    last_reconnected_at: datetime | None = None
    last_error: str | None = None

    def start(self) -> object:
        _call_optional(self.resource, "start")
        self.status, self.starts, self.last_started_at = "ready", self.starts + 1, _now()
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
            replacement = None if self.factory is None else self.factory.create_connection()
            if replacement is not None: self.resource = replacement
            self.start()
        else:
            self.status = "ready"
        self.reconnects += 1
        self.last_reconnected_at = _now()
        return self.resource

    def record_error(self, error: Exception) -> None:
        self.errors, self.status, self.last_error = self.errors + 1, "error", str(error)

    def health(self) -> Mapping[str, object]:
        payload: dict[str, object] = {"key": self.key, "role": self.role, "status": self.status, "starts": self.starts, "reconnects": self.reconnects, "errors": self.errors, "last_started_at": None if self.last_started_at is None else self.last_started_at.isoformat(), "last_reconnected_at": None if self.last_reconnected_at is None else self.last_reconnected_at.isoformat(), "last_error": self.last_error}
        check = getattr(self.resource, "health", None)
        if callable(check):
            try: value = check()
            except Exception as error: self.record_error(error)
            else:
                if isinstance(value, Mapping): payload["resource"] = dict(value)
        return payload


class IntegrationConnectionScope:
    def __init__(self) -> None:
        self._connections: dict[str, ScopedConnection] = {}
        self._started = False

    def register(self, key: str, resource: object, *, role: str = "resource") -> object:
        normalized = _key(key)
        existing = self._connections.get(normalized)
        if existing is not None: return existing.resource
        connection = ScopedConnection(normalized, role, resource)
        self._connections[normalized] = connection
        if self._started: connection.start()
        return resource

    def resolve(self, key: str, *, role: str, factory: object) -> object | None:
        normalized = _key(key)
        existing = self._connections.get(normalized)
        if existing is not None: return existing.resource
        resource = factory.create_connection()
        if resource is None: return None
        self._connections[normalized] = ScopedConnection(normalized, role, resource, factory=factory)
        if self._started: self._connections[normalized].start()
        return resource

    def get(self, key: str) -> object | None:
        connection = self._connections.get(_key(key))
        return None if connection is None else connection.resource

    def remove(self, key: str) -> object | None:
        connection = self._connections.pop(_key(key), None)
        if connection is None: return None
        connection.stop()
        return connection.resource

    def start(self) -> None:
        self._started = True
        for connection in self._connections.values():
            if connection.status != "ready": connection.start()

    def stop(self) -> None:
        for connection in reversed(tuple(self._connections.values())): connection.stop()
        self._started = False

    def start_roles(self, roles: tuple[str, ...]) -> None:
        owned = set(roles)
        for connection in self._connections.values():
            if connection.role in owned and connection.status != "ready": connection.start()

    def stop_roles(self, roles: tuple[str, ...]) -> None:
        owned = set(roles)
        for connection in reversed(tuple(self._connections.values())):
            if connection.role in owned and connection.status != "stopped": connection.stop()

    def reconnect(self, key: str | None = None) -> object | None:
        if key is not None: return self._connections[_key(key)].reconnect()
        for connection in self._connections.values(): connection.reconnect()
        return None

    def record_error(self, key: str, error: Exception) -> None:
        connection = self._connections.get(_key(key))
        if connection is not None: connection.record_error(error)

    def health(self) -> Mapping[str, object]:
        items = tuple(connection.health() for connection in self._connections.values())
        status = "ready" if self._started else ("stopped" if items else "ready")
        if any(item.get("status") == "error" for item in items): status = "degraded"
        return {"status": status, "connections": len(items), "items": items}


DefaultConnectionManager = IntegrationConnectionScope
ManagedConnection = ScopedConnection

__all__ = ["DefaultConnectionManager", "IntegrationConnectionScope", "ManagedConnection", "NoopConnectionManager", "ScopedConnection"]
