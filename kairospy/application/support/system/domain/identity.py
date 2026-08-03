from __future__ import annotations

from dataclasses import dataclass

from kairospy.application.support.runtime.domain.modes import RuntimeMode


@dataclass(frozen=True, slots=True)
class SystemIdentity:
    """Stable identity of one running trading system instance."""

    launch_id: str
    mode: RuntimeMode | str

    def __post_init__(self) -> None:
        launch_id = self.launch_id.strip()
        if not launch_id:
            raise ValueError("system launch_id is required")
        object.__setattr__(self, "launch_id", launch_id)
        object.__setattr__(self, "mode", RuntimeMode(self.mode))


__all__ = ["SystemIdentity"]
