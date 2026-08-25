"""Structured finite operations run by the Workbench Textual adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

from textual.worker import Worker

from .results import ResultRoute


@dataclass(frozen=True, slots=True)
class OperationSpec:
    """One immutable operation intent from confirmation through terminal result."""

    operation_id: str
    action_name: str
    audit_summary: str
    route: ResultRoute
    operation: Callable[[], Any]
    running_status: str
    equivalent_command: tuple[str, ...] | None = None

    @classmethod
    def create(
        cls,
        *,
        action_name: str,
        audit_summary: str,
        route: ResultRoute,
        operation: Callable[[], Any],
        running_status: str,
        equivalent_command: tuple[str, ...] | None = None,
    ) -> "OperationSpec":
        return cls(
            operation_id=f"operation-{uuid4().hex[:12]}",
            action_name=action_name,
            audit_summary=audit_summary,
            route=route,
            operation=operation,
            running_status=running_status,
            equivalent_command=equivalent_command,
        )


@dataclass(slots=True)
class RunningTask:
    """Bind the immutable intent to its one active Textual Worker."""

    spec: OperationSpec
    worker: Worker[Any]


__all__ = ["OperationSpec", "RunningTask"]
