from __future__ import annotations

from enum import StrEnum


class StrategyLifecycle(StrEnum):
    CREATED = "created"
    WAITING_FOR_DEPENDENCIES = "waiting_for_dependencies"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"

