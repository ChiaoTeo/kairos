from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from kairospy.application.system import ComponentProcessApplication
from kairospy.surface.console.models import ObserveSnapshot


class ObserveReader(Protocol):
    def read(self) -> ObserveSnapshot: ...


@dataclass(frozen=True, slots=True)
class SystemObserveReader:
    """Read-only adapter over the System application boundary."""

    processes: ComponentProcessApplication
    workspace_id: str

    def read(self) -> ObserveSnapshot:
        components = self.processes.list_status()
        market_snapshot: Mapping[str, Any] | None = None
        market = components.get("market", {})
        if market.get("status") in {"ok", "ready", "running", "degraded"}:
            try:
                socket = self.processes.workspace.paths.process_socket("market")
                market_snapshot = self.processes.client("market", socket, timeout=self.processes.control_timeout).snapshot()
            except Exception as error:
                market_snapshot = {"error": str(error)}
        return ObserveSnapshot(
            workspace_id=self.workspace_id,
            components=components,
            market_snapshot=market_snapshot,
        )
