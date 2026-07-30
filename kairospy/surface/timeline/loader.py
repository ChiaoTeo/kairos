from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kairospy.application.system.projectors.service import LaunchProjectionService
from kairospy.application.system.projectors.service import find_latest_instance, list_instances


@dataclass(frozen=True, slots=True)
class TimelineDataLoader:
    instance_path: Path

    def load(self) -> dict[str, object]:
        return LaunchProjectionService(self.instance_path).load_timeline_view()


__all__ = ["TimelineDataLoader", "find_latest_instance", "list_instances"]
