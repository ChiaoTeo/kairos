from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.application.support.runtime.domain.modes import RuntimeMode
from kairospy.application.support.runtime.domain.events import RuntimeEnvelope
from kairospy.application.support.runtime.domain.lines import RuntimeEventLine
from kairospy.application.support.runtime.services.orchestration.state import RuntimeStores
from kairospy.application.support.runtime.application.projection import RuntimeProjector
from kairospy.application.usecases.strategy.protocol import Strategy


@dataclass(frozen=True, slots=True)
class RuntimeLaunchSpec:
    launch_id: str
    mode: RuntimeMode | str
    strategy: Strategy
    source: RuntimeEventLine | None = None
    stores: RuntimeStores | None = None
    pre_events: tuple[RuntimeEnvelope, ...] = ()
    started_at: datetime | None = None
    processors: RuntimeProjector | None = None

    def __post_init__(self) -> None:
        if not self.launch_id.strip():
            raise ValueError("launch_id is required")
        object.__setattr__(self, "mode", RuntimeMode(self.mode))


__all__ = ["RuntimeLaunchSpec"]
