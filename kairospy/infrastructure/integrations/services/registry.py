from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from kairospy.infrastructure.integrations.domain import ParticipantRef, integration_key
from kairospy.infrastructure.integrations.protocols import IntegrationParticipant


@dataclass(slots=True)
class IntegrationRegistry:
    _items: dict[str, IntegrationParticipant] = field(default_factory=dict)

    def register(self, integration: IntegrationParticipant) -> None:
        name = integration_key(integration.name)
        existing = self._items.get(name)
        if existing is not None and existing is not integration:
            raise ValueError(f"integration {name!r} is already registered")
        self._items[name] = integration

    def get(self, name: str) -> IntegrationParticipant:
        key = integration_key(name)
        try:
            return self._items[key]
        except KeyError as error:
            raise KeyError(f"unknown integration: {name}") from error

    def provider(self, name: str) -> IntegrationParticipant:
        return self.get(name)

    def participant(self, ref: ParticipantRef) -> IntegrationParticipant:
        return self.get(ref.name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._items))

    @classmethod
    def with_items(cls, integrations: Iterable[IntegrationParticipant]) -> "IntegrationRegistry":
        registry = cls()
        for integration in integrations:
            registry.register(integration)
        return registry

    @classmethod
    def with_providers(cls, providers: Iterable[IntegrationParticipant]) -> "IntegrationRegistry":
        return cls.with_items(providers)
