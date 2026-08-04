from __future__ import annotations

from pathlib import Path
from typing import Callable
from kairospy.application.support.launch.application.commands import SystemCommandHandler

from kairospy.application.support.launch.domain.modes import RuntimeMode
from kairospy.application.support.launch.application.protocol import LaunchTargetFactory
from .daemon import LaunchDaemonResult, LaunchDaemonService
from .registry import LaunchRecord, LaunchRegistry
from kairospy.application.support.launch.application.configuration import SYSTEM_LAUNCH_ID


class LaunchControl:
    def __init__(self, root: str | Path = ".kairos/launches", *, target_factory: LaunchTargetFactory | None = None, command_dispatcher_factory: Callable[[Path], SystemCommandHandler] | None = None) -> None:
        self.root = Path(root).expanduser()
        self._daemon = LaunchDaemonService(self.root, target_factory=target_factory, command_dispatcher_factory=command_dispatcher_factory)
        self._registry = LaunchRegistry(self.root)

    def launch_foreground(
        self,
        *,
        mode: RuntimeMode | str,
        config_path: str | Path,
        launch_id: str | None = None,
        strategy_ref: str | None = None,
    ) -> LaunchDaemonResult:
        return self._daemon.launch_foreground(mode=mode, config_path=config_path, launch_id=launch_id, strategy_ref=strategy_ref)

    def start_background(
        self,
        *,
        mode: RuntimeMode | str,
        config_path: str | Path,
        launch_id: str | None = None,
        strategy_ref: str | None = None,
    ) -> LaunchDaemonResult:
        return self._daemon.start_background(mode=mode, config_path=config_path, launch_id=launch_id, strategy_ref=strategy_ref)

    def launch_system_foreground(self, *, launch_id: str = SYSTEM_LAUNCH_ID) -> LaunchDaemonResult:
        return self._daemon.launch_system_foreground(launch_id=launch_id)

    def start_system_background(self, *, launch_id: str = SYSTEM_LAUNCH_ID) -> LaunchDaemonResult:
        return self._daemon.start_system_background(launch_id=launch_id)

    def request_stop(self, *, mode: RuntimeMode | str, launch_id: str, reason: str, actor: str = "cli") -> Path:
        runtime_mode = mode.value if isinstance(mode, RuntimeMode) else str(mode)
        return self._registry.request_stop(mode=runtime_mode, launch_id=launch_id, reason=reason, actor=actor)

    def submit_command(
        self,
        *,
        mode: RuntimeMode | str,
        launch_id: str,
        kind: str,
        payload: dict[str, object] | None = None,
        actor: str = "cli",
    ) -> dict[str, object]:
        runtime_mode = mode.value if isinstance(mode, RuntimeMode) else str(mode)
        return self._registry.submit_command(mode=runtime_mode, launch_id=launch_id, kind=kind, payload=payload, actor=actor)

    def list(
        self,
        *,
        mode: RuntimeMode | str | None = None,
        launch_id: str | None = None,
        stale_after_seconds: float = 5.0,
    ) -> tuple[LaunchRecord, ...]:
        _ = stale_after_seconds
        runtime_mode = None if mode is None else mode.value if isinstance(mode, RuntimeMode) else str(mode)
        return self._registry.list(mode=runtime_mode, launch_id=launch_id)


from .facade import LaunchApplication


__all__ = ["LaunchApplication", "LaunchControl"]
