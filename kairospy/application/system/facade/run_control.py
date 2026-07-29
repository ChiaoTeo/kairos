from __future__ import annotations

from pathlib import Path

from kairospy.application.runtime import RuntimeMode
from kairospy.application.system.control.daemon import RunDaemonResult, RunDaemonService
from kairospy.application.system.control.registry import RunRecord, RunRegistry


class RunControl:
    def __init__(self, root: str | Path = ".kairos/runs") -> None:
        self.root = Path(root).expanduser()
        self._daemon = RunDaemonService(self.root)
        self._registry = RunRegistry(self.root)

    def run_foreground(
        self,
        *,
        mode: RuntimeMode | str,
        config_path: str | Path,
        run_id: str | None = None,
    ) -> RunDaemonResult:
        return self._daemon.run_foreground(mode=mode, config_path=config_path, run_id=run_id)

    def start_background(
        self,
        *,
        mode: RuntimeMode | str,
        config_path: str | Path,
        run_id: str | None = None,
    ) -> RunDaemonResult:
        return self._daemon.start_background(mode=mode, config_path=config_path, run_id=run_id)

    def request_stop(self, *, mode: RuntimeMode | str, run_id: str, reason: str, actor: str = "cli") -> Path:
        runtime_mode = mode.value if isinstance(mode, RuntimeMode) else str(mode)
        return self._registry.request_stop(mode=runtime_mode, run_id=run_id, reason=reason, actor=actor)

    def list(
        self,
        *,
        mode: RuntimeMode | str | None = None,
        run_id: str | None = None,
        stale_after_seconds: float = 5.0,
    ) -> tuple[RunRecord, ...]:
        _ = stale_after_seconds
        runtime_mode = None if mode is None else mode.value if isinstance(mode, RuntimeMode) else str(mode)
        return self._registry.list(mode=runtime_mode, run_id=run_id)


__all__ = ["RunControl"]
