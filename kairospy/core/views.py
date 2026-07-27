from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Any, Iterable, Literal, Mapping
from uuid import UUID


ViewOwner = Literal["system", "project", "strategy"]
ViewMutability = Literal["read_only", "runtime_writable", "strategy_writable"]
ViewPersistence = Literal["ephemeral", "checkpointed", "journaled"]


@dataclass(frozen=True, slots=True)
class ViewFieldSchema:
    name: str
    semantic: str = ""
    time_semantics: str = ""
    evidence: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("view field name is required")


@dataclass(frozen=True, slots=True)
class ViewSchema:
    key: str
    owner: ViewOwner | str
    version: str = "1"
    fields: tuple[ViewFieldSchema, ...] = ()
    mutability: ViewMutability = "read_only"
    persistence: ViewPersistence = "ephemeral"
    evidence: str = ""

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("view schema key is required")
        if not self.owner.strip():
            raise ValueError("view schema owner is required")
        if not self.version.strip():
            raise ValueError("view schema version is required")
        object.__setattr__(self, "fields", tuple(_field_schema(item) for item in self.fields))

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.fields)

    @property
    def schema_hash(self) -> str:
        return view_hash(self)


@dataclass(frozen=True, slots=True)
class ViewEnvelope:
    key: str
    schema_version: str
    owner: str
    payload: Any
    as_of: datetime | None = None
    available_time: datetime | None = None
    payload_hash: str = ""

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.schema_version.strip() or not self.owner.strip():
            raise ValueError("view envelope identity fields are required")
        if self.as_of is not None and self.as_of.tzinfo is None:
            raise ValueError("view as_of must be timezone-aware")
        if self.available_time is not None and self.available_time.tzinfo is None:
            raise ValueError("view available_time must be timezone-aware")
        if not self.payload_hash:
            object.__setattr__(self, "payload_hash", view_hash(self.payload))

    @property
    def view_hash(self) -> str:
        return view_hash(
            {
                "key": self.key,
                "schema_version": self.schema_version,
                "owner": self.owner,
                "as_of": self.as_of,
                "available_time": self.available_time,
                "payload_hash": self.payload_hash,
            }
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "schema_version": self.schema_version,
            "owner": self.owner,
            "as_of": self.as_of.isoformat() if self.as_of is not None else None,
            "available_time": self.available_time.isoformat() if self.available_time is not None else None,
            "payload_hash": self.payload_hash,
            "view_hash": self.view_hash,
        }


@dataclass(frozen=True, slots=True)
class StrategyRunView:
    strategy_id: str
    event_count: int = 0
    runtime_event_count: int = 0
    last_event_time: datetime | None = None
    last_stream: str | None = None
    last_runtime_event_time: datetime | None = None
    last_runtime_stream: str | None = None
    status: str = "initialized"


@dataclass(frozen=True, slots=True)
class IntentStateSummary:
    intent_id: str
    instrument_id: str
    status: str
    active: bool
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class IntentJournalView:
    total_count: int = 0
    active_count: int = 0
    states: tuple[IntentStateSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class ControlRequestSummary:
    request_id: str
    strategy_id: str
    kind: str
    requested_at: datetime | None = None
    payload: tuple[tuple[str, object], ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ControlJournalView:
    total_count: int = 0
    requests: tuple[ControlRequestSummary, ...] = ()


class ViewRegistry:
    def __init__(self, schemas: Iterable[ViewSchema] = ()) -> None:
        self._schemas: dict[str, ViewSchema] = {}
        for schema in schemas:
            self.register(schema)

    def register(self, schema: ViewSchema) -> ViewSchema:
        existing = self._schemas.get(schema.key)
        if existing is not None and existing != schema:
            raise ValueError(f"view schema {schema.key!r} is already registered")
        self._schemas[schema.key] = schema
        return schema

    def require(self, key: str) -> ViewSchema:
        try:
            return self._schemas[key]
        except KeyError as error:
            raise KeyError(f"unknown view schema: {key}") from error

    def get(self, key: str) -> ViewSchema | None:
        return self._schemas.get(key)

    def schemas(self) -> Mapping[str, ViewSchema]:
        return MappingProxyType(dict(sorted(self._schemas.items())))


class ViewStore:
    def __init__(
        self,
        registry: ViewRegistry | None = None,
        envelopes: Iterable[ViewEnvelope] = (),
    ) -> None:
        self.registry = registry or default_view_registry()
        self._envelopes: dict[str, ViewEnvelope] = {}
        for envelope in envelopes:
            self._envelopes[envelope.key] = envelope

    def register(self, schema: ViewSchema) -> ViewSchema:
        return self.registry.register(schema)

    def put(
        self,
        key: str,
        payload: Any,
        *,
        as_of: datetime | None = None,
        available_time: datetime | None = None,
    ) -> ViewEnvelope:
        schema = self.registry.require(key)
        if schema.mutability != "strategy_writable":
            raise PermissionError(f"strategy cannot write view {key!r}")
        return self._put(schema, payload, as_of=as_of, available_time=available_time)

    def put_runtime(
        self,
        key: str,
        payload: Any,
        *,
        as_of: datetime | None = None,
        available_time: datetime | None = None,
    ) -> ViewEnvelope:
        return self._put(self.registry.require(key), payload, as_of=as_of, available_time=available_time)

    def get(self, key: str, default: Any = None) -> Any:
        envelope = self._envelopes.get(key)
        return default if envelope is None else envelope.payload

    def require(self, key: str) -> Any:
        try:
            return self._envelopes[key].payload
        except KeyError as error:
            raise KeyError(f"view has no value: {key}") from error

    def envelope(self, key: str) -> ViewEnvelope | None:
        return self._envelopes.get(key)

    def envelopes(self) -> Mapping[str, ViewEnvelope]:
        return MappingProxyType(dict(sorted(self._envelopes.items())))

    @property
    def view_hashes(self) -> Mapping[str, str]:
        return MappingProxyType({key: value.view_hash for key, value in sorted(self._envelopes.items())})

    @property
    def context_hash(self) -> str:
        return view_hash(dict(self.view_hashes))

    def snapshot(self) -> dict[str, object]:
        return {
            "schemas": {
                key: {
                    "owner": schema.owner,
                    "version": schema.version,
                    "mutability": schema.mutability,
                    "persistence": schema.persistence,
                    "schema_hash": schema.schema_hash,
                }
                for key, schema in self.registry.schemas().items()
            },
            "views": {key: envelope.to_dict() for key, envelope in self.envelopes().items()},
            "context_hash": self.context_hash,
        }

    def _put(
        self,
        schema: ViewSchema,
        payload: Any,
        *,
        as_of: datetime | None,
        available_time: datetime | None,
    ) -> ViewEnvelope:
        envelope = ViewEnvelope(
            key=schema.key,
            schema_version=schema.version,
            owner=str(schema.owner),
            payload=payload,
            as_of=as_of,
            available_time=available_time,
        )
        self._envelopes[schema.key] = envelope
        return envelope


def default_view_registry() -> ViewRegistry:
    return ViewRegistry(
        (
            ViewSchema(
                "system.strategy",
                "system",
                fields=(
                    ViewFieldSchema("strategy_id", "strategy identity", "run identity", "runtime"),
                    ViewFieldSchema("event_count", "consumed market event count", "runtime sequence", "runtime"),
                    ViewFieldSchema("runtime_event_count", "consumed runtime event count", "runtime sequence", "runtime"),
                    ViewFieldSchema("last_event_time", "latest market event time", "event time", "market event source"),
                    ViewFieldSchema("last_stream", "latest market event stream", "event time", "market event source"),
                    ViewFieldSchema("last_runtime_event_time", "latest runtime event time", "event time", "runtime event source"),
                    ViewFieldSchema("last_runtime_stream", "latest runtime event stream", "event time", "runtime event source"),
                    ViewFieldSchema("status", "runtime status", "runtime time", "runtime"),
                ),
                evidence="strategy runtime loop",
            ),
            ViewSchema(
                "system.data",
                "system",
                fields=(
                    ViewFieldSchema("bindings", "data bindings", "binding time", "DataContext"),
                ),
                evidence="data context snapshot",
            ),
            ViewSchema(
                "system.intents",
                "system",
                fields=(
                    ViewFieldSchema("total_count", "known strategy intent count", "runtime state", "IntentJournal"),
                    ViewFieldSchema("active_count", "active strategy intent count", "runtime state", "IntentJournal"),
                    ViewFieldSchema("states", "strategy intent state summaries", "runtime state", "IntentJournal"),
                ),
                evidence="intent journal projection",
            ),
            ViewSchema(
                "system.control",
                "system",
                fields=(
                    ViewFieldSchema("total_count", "control request count", "runtime state", "ControlJournal"),
                    ViewFieldSchema("requests", "strategy runtime control requests", "request time", "ControlJournal"),
                ),
                evidence="control request journal projection",
            ),
        )
    )


def view_hash(value: object) -> str:
    return sha256(json.dumps(_primitive(value), sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _field_schema(value: ViewFieldSchema | Mapping[str, Any]) -> ViewFieldSchema:
    if isinstance(value, ViewFieldSchema):
        return value
    return ViewFieldSchema(**dict(value))


def _primitive(value: Any) -> Any:
    if is_dataclass(value):
        return {field.name: _primitive(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_primitive(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (Decimal, datetime, UUID)):
        return str(value)
    if hasattr(value, "to_dict"):
        return _primitive(value.to_dict())
    if hasattr(value, "__dict__"):
        return _primitive(vars(value))
    return value


__all__ = [
    "ControlJournalView",
    "ControlRequestSummary",
    "IntentJournalView",
    "IntentStateSummary",
    "StrategyRunView",
    "ViewEnvelope",
    "ViewFieldSchema",
    "ViewMutability",
    "ViewOwner",
    "ViewPersistence",
    "ViewRegistry",
    "ViewSchema",
    "ViewStore",
    "default_view_registry",
    "view_hash",
]
