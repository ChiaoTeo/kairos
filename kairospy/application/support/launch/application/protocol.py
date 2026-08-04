from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from kairospy.application.support.launch.domain.modes import RuntimeMode


@runtime_checkable
class StopSignalBindable(Protocol):
    """Runtime capability required by a launch-managed event source."""

    def set_stop_signal(self, stop_requested: Callable[[], bool] | None) -> None: ...


@dataclass(frozen=True, slots=True)
class LaunchRequest:
    """Business request used to resolve a runnable launch target."""

    mode: RuntimeMode
    config_path: Path
    strategy_ref: str | None = None
    launch_directory: Path | None = None


@dataclass(frozen=True, slots=True)
class LaunchTarget:
    """Resolved target exposed to launch control."""

    mode: RuntimeMode
    launch_id: str
    launch_directory: Path
    _runner: Callable[[], object]
    _bind_stop: Callable[[Callable[[], bool]], None]

    def run(self) -> object:
        return self._runner()

    def bind_stop(self, stop_requested: Callable[[], bool]) -> None:
        self._bind_stop(stop_requested)


@dataclass(frozen=True, slots=True)
class LaunchTargetDescriptor:
    mode: RuntimeMode
    launch_id: str
    launch_directory: Path


class LaunchTargetFactory(Protocol):
    """The single composition boundary used by launch control."""

    def resolve(self, request: LaunchRequest) -> LaunchTarget: ...

    def describe(self, *, mode: RuntimeMode, config_path: Path) -> LaunchTargetDescriptor: ...

    def launch_system(self, *, launch_id: str, launch_directory: Path) -> object: ...

    def launch_events(self, *, strategy_path: str, events_path: Path, launch_id: str, mode: RuntimeMode) -> object: ...

    def open_system_session(self, *, strategy_path: str, launch_id: str, mode: RuntimeMode) -> object: ...


__all__ = ["LaunchRequest", "LaunchTarget", "LaunchTargetDescriptor", "LaunchTargetFactory", "StopSignalBindable"]
