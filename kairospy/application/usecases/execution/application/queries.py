from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from kairospy.application.usecases.execution.services.queries import OrderQueryService


@dataclass(frozen=True, slots=True)
class ExecutionOrderQueries:
    """Public read-side application API for execution views."""

    source: object

    def status(self, order_id: str) -> Mapping[str, object]:
        return OrderQueryService(self.source).status(order_id)


__all__ = ["ExecutionOrderQueries"]
