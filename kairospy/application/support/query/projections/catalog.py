from __future__ import annotations

"""Catalog of system launch read models."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProjectionSpec:
    name: str
    kind: str
    title: str
    resource: str
    record_key: str | None = None


LAUNCH_PROJECTIONS: tuple[ProjectionSpec, ...] = (
    ProjectionSpec("run.summary", "snapshot", "Run summary", "summary.json"),
    ProjectionSpec("run.metrics", "snapshot", "Run metrics", "metrics.json"),
    ProjectionSpec("run.config", "snapshot", "Normalized run config", "config.normalized.json"),
    ProjectionSpec("run.records", "history", "Run records", "records.jsonl", "timelineRecords"),
    ProjectionSpec("run.equity", "derived-history", "Equity history", "equity.jsonl", "equity"),
    ProjectionSpec("run.fills", "derived-history", "Fills", "fills.jsonl", "fills"),
    ProjectionSpec("run.intents", "derived-history", "Intent states", "intent_states.jsonl", "intents"),
    ProjectionSpec("run.trades", "derived-history", "Trades", "trades.jsonl", "trades"),
    ProjectionSpec("run.account.current", "current", "Current account state", "account/current.json"),
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
