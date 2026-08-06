from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kairospy.application.support.composition.application.projections import (
    find_latest_instance,
    launch_projection_query,
    list_instances,
)


@dataclass(frozen=True, slots=True)
class TimelineDataLoader:
    instance_path: Path

    def load(self) -> dict[str, object]:
        return launch_projection_query(self.instance_path).load_run_view()


__all__ = ["TimelineDataLoader", "find_latest_instance", "list_instances"]
