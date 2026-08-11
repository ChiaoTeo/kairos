from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping


COMPONENTS = ("reference", "market", "account", "risk", "execution")


@dataclass(frozen=True, slots=True)
class ObserveSnapshot:
    """UI read model; it contains no mutable business state."""

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
        if any(
            status in {"failed", "unresponsive", "unhealthy", "stale"}
            for status in statuses
        ):
            return "degraded"
        if any(
            status in {"recovering", "not_running", "unknown"} for status in statuses
        ):
            return "partial"
        return "healthy"


def component_rows(snapshot: ObserveSnapshot) -> tuple[tuple[str, str, str, str], ...]:
    """Return stable, presentation-friendly component rows."""
    rows: list[tuple[str, str, str, str]] = []
    for name in COMPONENTS:
        value = snapshot.components.get(name, {})
        status = str(value.get("status", "unknown"))
        freshness = _freshness(value)
        detail = str(value.get("error") or _detail(value))
        rows.append((name, status, freshness, detail))
    return tuple(rows)


def launch_rows(snapshot: ObserveSnapshot) -> tuple[tuple[str, str, str, str], ...]:
    """Return launch identity and lifecycle state without probing processes."""

    ordered = sorted(
        enumerate(snapshot.launches),
        key=lambda item: (
            str(item[1].get("updated_at") or item[1].get("created_at") or ""),
            item[0],
        ),
        reverse=True,
    )
    return tuple(
        (
            str(value.get("launch_id", "-")),
            str(value.get("mode", "-")),
            str(value.get("state", "unknown")),
            str(value.get("instance_id", "-")),
        )
        for _index, value in ordered
    )


def recommended_action(snapshot: ObserveSnapshot) -> str:
    """Return one safe CLI action based on the most recent observed state."""

    if snapshot.error:
        return "kairos project doctor"
    if not snapshot.launches:
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


def _freshness(value: Mapping[str, Any]) -> str:
    for key in ("last_event_age_ms", "freshness_age_ms", "age_ms"):
        if value.get(key) is not None:
            try:
                return f"{float(value[key]) / 1000:.1f}s ago"
            except (TypeError, ValueError):
                pass
    for key in ("freshness", "data_health", "readiness"):
        if value.get(key) is not None:
            return str(value[key])
    return "-"


def _detail(value: Mapping[str, Any]) -> str:
    if value.get("error"):
        return str(value["error"])
    if value.get("pid"):
        return f"pid={value['pid']}"
    return "-"
