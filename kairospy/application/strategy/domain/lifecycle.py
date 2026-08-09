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


class StrategyReadiness(StrEnum):
    """What the strategy has proven about its startup dependencies."""

    NOT_STARTED = "not_started"
    WAITING_FOR_DEPENDENCIES = "waiting_for_dependencies"
    SUBSCRIPTIONS_ACTIVE = "subscriptions_active"
    SNAPSHOT_READY = "snapshot_ready"
    READY = "ready"


class StrategyDataHealth(StrEnum):
    """Data-plane health after a strategy has been enabled."""

    NOT_STARTED = "not_started"
    WAITING_FOR_DATA = "waiting_for_data"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
