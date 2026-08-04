from __future__ import annotations

from dataclasses import dataclass

from kairospy.application.support.launch.domain.modes import RuntimeMode


@dataclass(frozen=True, slots=True)
class LaunchIdentity:
    """Stable logical identity of one launch target."""

    launch_id: str
    mode: RuntimeMode | str

    def __post_init__(self) -> None:
        launch_id = self.launch_id.strip()
        if not launch_id:
            raise ValueError("launch_id is required")
        object.__setattr__(self, "launch_id", launch_id)
        object.__setattr__(self, "mode", RuntimeMode(self.mode))


# Kept as a semantic alias for system code while the system root is being
# reduced to runtime control. The identity itself belongs to launch.
SystemIdentity = LaunchIdentity


__all__ = ["LaunchIdentity", "SystemIdentity"]
