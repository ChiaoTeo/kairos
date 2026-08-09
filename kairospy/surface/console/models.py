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
    market_snapshot: Mapping[str, Any] | None = None
    error: str | None = None
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def overall_status(self) -> str:
        if not self.components:
            return "degraded" if self.error else "partial"
        statuses = {str(value.get("status", "unknown")) for value in self.components.values()}
        if any(status in {"failed", "unresponsive", "unhealthy", "stale"} for status in statuses):
            return "degraded"
        if any(status in {"recovering", "not_running", "unknown"} for status in statuses):
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
