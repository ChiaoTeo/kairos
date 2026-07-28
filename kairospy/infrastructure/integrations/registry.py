from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .protocols import IntegrationAdapter


@dataclass(slots=True)
class IntegrationRegistry:
    _items: dict[str, IntegrationAdapter] = field(default_factory=dict)

    def register(self, integration: IntegrationAdapter) -> None:
        name = _clean_name(integration.name)
        existing = self._items.get(name)
        if existing is not None and existing is not integration:
            raise ValueError(f"integration {name!r} is already registered")
        self._items[name] = integration

    def get(self, name: str) -> IntegrationAdapter:
        key = _clean_name(name)
        try:
            return self._items[key]
        except KeyError as error:
            raise KeyError(f"unknown integration: {name}") from error

    def provider(self, name: str) -> IntegrationAdapter:
        return self.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._items))

    @classmethod
    def with_items(cls, integrations: Iterable[IntegrationAdapter]) -> "IntegrationRegistry":
        registry = cls()
        for integration in integrations:
            registry.register(integration)
        return registry

    @classmethod
    def with_providers(cls, providers: Iterable[IntegrationAdapter]) -> "IntegrationRegistry":
        return cls.with_items(providers)


def _clean_name(name: str) -> str:
    value = str(name).strip()
    if not value:
        raise ValueError("integration name cannot be empty")
    return value
