from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class InstanceState(StrEnum):
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class LaunchIdentity:
    launch_id: str
    mode: str

    def __post_init__(self) -> None:
        if not self.launch_id.strip() or not self.mode.strip():
            raise ValueError("launch_id and mode are required")


@dataclass(frozen=True, slots=True)
class LaunchInstance:
    identity: LaunchIdentity
    instance_id: str
    directory: Path
    control_socket: Path
    state: InstanceState = InstanceState.CREATED

    def __post_init__(self) -> None:
        if not self.instance_id.strip():
            raise ValueError("instance_id is required")
        if self.directory == self.directory.parent:
            raise ValueError("instance directory must be scoped below a launch")

