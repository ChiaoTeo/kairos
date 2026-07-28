from __future__ import annotations

from kairospy.application.runtime.services.component import RuntimeViewPublisher
from kairospy.core.execution import ExecutionCoordinator


def execution_coordinator_components(coordinator: ExecutionCoordinator) -> tuple[RuntimeViewPublisher, ...]:
    from kairospy.application.runtime.projection.execution import ExecutionCurrentProjection
    from kairospy.application.runtime.projection.order import OrderCurrentProjection

    return (
        ExecutionCurrentProjection(coordinator),
        OrderCurrentProjection(coordinator),
    )


__all__ = ["execution_coordinator_components"]
