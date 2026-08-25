"""Workspace-wide, read-only System observation use case."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from kairospy.system.apps.components.application import ComponentProcessApplication
from kairospy.system.apps.launch.application import LaunchRegistryApplication
from kairospy.system.domain.workspace import Workspace


@dataclass(frozen=True, slots=True)
class ObserveSnapshot:
    """Typed result of observing one workspace at a point in time."""

    workspace_id: str
    components: Mapping[str, Mapping[str, Any]]
    launches: tuple[Mapping[str, Any], ...] = ()
    market_snapshot: Mapping[str, Any] | None = None
    error: str | None = None
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def overall_status(self) -> str:
        launch_states = {str(value.get("state", "unknown")) for value in self.launches}
        if launch_states.intersection({"failed", "unresponsive", "degraded"}):
            return "degraded"
        if not self.components:
            return "degraded" if self.error else "partial"
        statuses = {
            str(value.get("status", "unknown")) for value in self.components.values()
        }
        if statuses.intersection({"failed", "unresponsive", "unhealthy", "stale"}):
            return "degraded"
        if statuses.intersection({"recovering", "not_running", "unknown"}):
            return "partial"
        return "healthy"


@dataclass(frozen=True, slots=True)
class SystemObserveApplication:
    """Aggregate System-owned process facts and Launch registry facts."""

    workspace: Workspace

    def read(self) -> ObserveSnapshot:
        components = ComponentProcessApplication(self.workspace).list_status()
        market_snapshot: Mapping[str, Any] | None = None
        market = components.get("market", {})
        if market.get("status") in {"ok", "ready", "running", "degraded"}:
            market_snapshot = dict(market)
        return ObserveSnapshot(
            workspace_id=self.workspace.workspace_id,
            components=components,
            launches=tuple(LaunchRegistryApplication(self.workspace).list()),
            market_snapshot=market_snapshot,
        )
