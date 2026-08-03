from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType

from kairospy.domain.views import ViewSchema


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


__all__ = ["ViewRegistry"]
