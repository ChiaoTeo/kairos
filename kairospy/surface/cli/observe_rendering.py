"""CLI-only rendering of System observation results."""

from __future__ import annotations

from typing import Any

from kairospy.system.apps.observe.application import ObserveSnapshot


def observe_payload(snapshot: ObserveSnapshot) -> dict[str, Any]:
    """Return a machine-readable current topology without action heuristics."""

    return {
        "workspace_id": snapshot.workspace_id,
        "overall_status": snapshot.overall_status,
        "shared_services": snapshot.shared_services,
        "active_instances": snapshot.active_instances,
        "support_processes": snapshot.support_processes,
        "error": snapshot.error,
        "observed_at": snapshot.observed_at,
    }


__all__ = ["observe_payload"]
