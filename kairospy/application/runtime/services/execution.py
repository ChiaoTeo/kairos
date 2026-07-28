from __future__ import annotations

from typing import Protocol

from kairospy.core.execution import ExecutionCoordinator

from .component import RuntimeViewPublisher


class ExecutionService(Protocol):
    coordinator: ExecutionCoordinator

    def runtime_components(self) -> tuple[RuntimeViewPublisher, ...]:
        ...


__all__ = ["ExecutionService"]
