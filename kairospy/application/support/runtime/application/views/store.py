from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Any

from kairospy.domain.views import ViewEnvelope, ViewSchema, view_hash

from .defaults import default_view_registry
from .registry import ViewRegistry


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


__all__ = ["ViewStore"]
