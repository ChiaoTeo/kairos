from __future__ import annotations

from .registry import RunRecord, RunRegistry, list_run_daemons
from .state import JsonLiveRuntimeStateStore, LiveRuntimeStateSnapshot, LiveRuntimeStateStore


def __getattr__(name: str) -> object:
    if name in {"RunDaemonResult", "RunDaemonService"}:
        from .daemon import RunDaemonResult, RunDaemonService

        return {"RunDaemonResult": RunDaemonResult, "RunDaemonService": RunDaemonService}[name]
    raise AttributeError(name)


__all__ = [
    "RunDaemonResult",
    "RunDaemonService",
    "RunRecord",
    "RunRegistry",
    "JsonLiveRuntimeStateStore",
    "LiveRuntimeStateSnapshot",
    "LiveRuntimeStateStore",
    "list_run_daemons",
]
