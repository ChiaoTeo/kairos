from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from kairospy.application.system import ComponentProcessApplication
from kairospy.application.launch.application import LaunchRegistryApplication
from kairospy.surface.console.models import ObserveSnapshot


class ObserveReader(Protocol):
    def read(self) -> ObserveSnapshot: ...


@dataclass(frozen=True, slots=True)
class SystemObserveReader:
    """Read-only adapter over System and Launch application boundaries."""

    processes: ComponentProcessApplication
    workspace_id: str

    def read(self) -> ObserveSnapshot:
        components = self.processes.list_status()
        market_snapshot: Mapping[str, Any] | None = None
        market = components.get("market", {})
        if market.get("status") in {"ok", "ready", "running", "degraded"}:
            market_snapshot = dict(market)
        return ObserveSnapshot(
            workspace_id=self.workspace_id,
            components=components,
            launches=tuple(LaunchRegistryApplication(self.processes.workspace).list()),
            market_snapshot=market_snapshot,
        )
