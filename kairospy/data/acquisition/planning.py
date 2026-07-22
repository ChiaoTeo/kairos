from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping


@dataclass(frozen=True, slots=True)
class TaskRangePlan:
    start: datetime
    end: datetime
    tasks: int
    cached: int = 0

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise ValueError("task range plan requires start before end")
        if self.tasks < 0 or self.cached < 0:
            raise ValueError("task range counts cannot be negative")
        if self.cached > self.tasks:
            raise ValueError("cached task count cannot exceed total tasks")

    @property
    def uncached(self) -> int:
        return self.tasks - self.cached

    def to_primitive(self) -> dict[str, object]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "tasks": self.tasks,
            "cached": self.cached,
            "uncached": self.uncached,
        }


@dataclass(frozen=True, slots=True)
class UniversePlan:
    kind: str
    symbols: int
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("universe plan requires kind")
        if self.symbols < 0:
            raise ValueError("universe symbol count cannot be negative")


@dataclass(frozen=True, slots=True)
class DataProductTaskPlan:
    provider: str
    task_type: str
    ranges: tuple[TaskRangePlan, ...]
    universe: UniversePlan | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.task_type.strip():
            raise ValueError("data product task plan requires provider and task_type")

    @property
    def total_tasks(self) -> int:
        return sum(item.tasks for item in self.ranges)

    @property
    def cached_tasks(self) -> int:
        return sum(item.cached for item in self.ranges)

    @property
    def uncached_tasks(self) -> int:
        return sum(item.uncached for item in self.ranges)

    def to_primitive(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "provider": self.provider,
            "task_type": self.task_type,
            "total_tasks": self.total_tasks,
            "cached_tasks": self.cached_tasks,
            "uncached_tasks": self.uncached_tasks,
            "ranges": [item.to_primitive() for item in self.ranges],
        }
        if self.universe is not None:
            payload["universe"] = self.universe.kind
            payload["symbols"] = self.universe.symbols
            payload.update(dict(self.universe.metadata))
        payload.update(dict(self.metadata))
        return payload
