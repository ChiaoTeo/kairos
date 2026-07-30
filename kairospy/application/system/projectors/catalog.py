from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProjectionSpec:
    name: str
    kind: str
    title: str
    resource: str
    record_key: str | None = None


LAUNCH_PROJECTIONS: tuple[ProjectionSpec, ...] = (
    ProjectionSpec("launch.summary", "snapshot", "Launch summary", "summary.json"),
    ProjectionSpec("launch.metrics", "snapshot", "Launch metrics", "metrics.json"),
    ProjectionSpec("launch.config", "snapshot", "Normalized launch config", "config.normalized.json"),
    ProjectionSpec("launch.timeline", "history", "Launch timeline", "timeline.jsonl", "timelineRecords"),
    ProjectionSpec("timeline.decision_trace", "derived-history", "Decision trace", "decision_trace.jsonl", "decisionTrace"),
    ProjectionSpec("timeline.risk_snapshots", "derived-history", "Risk snapshots", "risk_snapshots.jsonl", "riskSnapshots"),
    ProjectionSpec("timeline.equity", "derived-history", "Equity history", "equity.jsonl", "equity"),
    ProjectionSpec("timeline.fills", "derived-history", "Fills", "fills.jsonl", "fills"),
    ProjectionSpec("timeline.intents", "derived-history", "Intent states", "intent_states.jsonl", "intents"),
    ProjectionSpec("timeline.trades", "derived-history", "Market records", "trades.jsonl", "trades"),
    ProjectionSpec("account.current", "current", "Current account state", "account/current.json"),
)


class LaunchProjectionCatalog:
    def __init__(self, specs: tuple[ProjectionSpec, ...] = LAUNCH_PROJECTIONS) -> None:
        self._specs = specs
        self._by_name = {spec.name: spec for spec in specs}

    def list(self) -> tuple[ProjectionSpec, ...]:
        return self._specs

    def require(self, name: str) -> ProjectionSpec:
        try:
            return self._by_name[name]
        except KeyError as error:
            raise ValueError(f"unknown launch projection: {name}") from error

    def history(self) -> tuple[ProjectionSpec, ...]:
        return tuple(spec for spec in self._specs if spec.record_key is not None)


__all__ = ["ProjectionSpec", "LAUNCH_PROJECTIONS", "LaunchProjectionCatalog"]
