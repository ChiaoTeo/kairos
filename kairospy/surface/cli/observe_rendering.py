"""CLI-only rendering of System observation results."""

from __future__ import annotations

from typing import Any

from kairospy.system.apps.observe.application import ObserveSnapshot


def observe_payload(snapshot: ObserveSnapshot) -> dict[str, Any]:
    return {
        "workspace_id": snapshot.workspace_id,
        "components": snapshot.components,
        "launches": snapshot.launches,
        "market_snapshot": snapshot.market_snapshot,
        "next_action": recommended_action(snapshot),
    }


def recommended_action(snapshot: ObserveSnapshot) -> str:
    if snapshot.error or not snapshot.launches:
        return "kairos project doctor"
    latest = max(
        enumerate(snapshot.launches),
        key=lambda item: (
            str(item[1].get("updated_at") or item[1].get("created_at") or ""),
            item[0],
        ),
    )[1]
    launch_id = str(latest.get("launch_id") or "").strip()
    state = str(latest.get("state") or "unknown")
    mode = str(latest.get("mode") or "")
    if not launch_id:
        return "kairos project doctor"
    if state in {"failed", "unresponsive", "degraded"}:
        return f"kairos launch logs {launch_id}"
    if state == "completed" and mode == "backtest":
        return f"kairos launch report {launch_id}"
    if state in {"starting", "ready", "running"}:
        return f"kairos launch status {launch_id}"
    return f"kairos launch start {launch_id}"


__all__ = ["observe_payload", "recommended_action"]
