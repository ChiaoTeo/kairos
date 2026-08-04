from __future__ import annotations

from dataclasses import dataclass, field

from kairospy.infrastructure.integrations.application.connections import (
    IntegrationConnection,
    IntegrationConnectionApplication,
    IntegrationConnectionSpec,
)
from kairospy.infrastructure.integrations.services.factories.registry import GatewayRegistry


@dataclass(slots=True)
class DefaultIntegrationConnectionApplication(IntegrationConnectionApplication):
    registry: GatewayRegistry = field(default_factory=GatewayRegistry.with_builtins)

    def connect(self, spec: IntegrationConnectionSpec) -> IntegrationConnection:
        return self.registry.create(spec)


__all__ = ["DefaultIntegrationConnectionApplication"]
