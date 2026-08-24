"""Snapshot and replay consumers over one shared logical read plan."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, AsyncIterator, Iterator, Mapping, Sequence

from .catalog import (
    DatasetCatalogApplication,
    event_payload,
    event_time,
    normalize_kind,
    observation_kind,
)
from .models import DatasetReadPlan, DatasetSetRef


_LEGACY_MARKET_SOURCE_PROVIDERS = {
    "binance": "binance",
    "binance-spot": "binance",
    "binance-spot-fixture": "binance",
    "binance-equity": "binance",
    "massive": "massive",
    "massive-equity": "massive",
    "massive-spy-fixture": "massive",
}


def _normalize_market_provider(event: Mapping[str, Any]) -> Mapping[str, Any]:
    """Read the retired Market source field only through an explicit map."""

    for envelope in ("Quote", "Trade", "Bar", "Greeks"):
        raw = event.get(envelope)
        if not isinstance(raw, Mapping) or "provider" in raw or "source_id" not in raw:
            continue
        legacy = str(raw["source_id"])
        provider = _LEGACY_MARKET_SOURCE_PROVIDERS.get(legacy)
        if provider is None:
            raise ValueError(
                f"legacy Market source_id {legacy!r} has no explicit provider mapping"
            )
        payload = dict(raw)
        payload.pop("source_id", None)
        payload["provider"] = provider
        normalized = dict(event)
        normalized[envelope] = payload
        return normalized
    return event


@dataclass(frozen=True, slots=True)
class _Fact:
    event: Mapping[str, Any]
    owner: str
    dataset_kind: str
    source: str
    dataset_identity: str
    ordinal: int

    @property
    def sort_key(self) -> tuple[Any, ...]:
        payload = event_payload(self.event)
        return (
            event_time(self.event) is None,
            event_time(self.event) or 0,
            self.owner,
            self.dataset_kind,
            str(payload.get("provider", self.source)),
            json.dumps(payload.get("scope", {}), sort_keys=True, separators=(",", ":")),
            str(payload.get("instrument_id", "")),
            self.dataset_identity,
            json.dumps(self.event, sort_keys=True, separators=(",", ":")),
            self.ordinal,
        )


@dataclass(frozen=True, slots=True)
class DatasetSnapshot:
    """Complete bounded logical view; records remain independent of files."""

    plan: DatasetReadPlan
    _facts: tuple[_Fact, ...]

    def scan(self, kind: str | None = None) -> tuple[Mapping[str, Any], ...]:
        normalized = normalize_kind(kind) if kind else None
        return tuple(
            fact.event
            for fact in self._facts
            if normalized is None or observation_kind(fact.event) == normalized
        )

    def __len__(self) -> int:
        return len(self._facts)


@dataclass(frozen=True, slots=True)
class DatasetAnalyticalView:
    """Lazy, bounded column projection over one shared DatasetReadPlan."""

    plan: DatasetReadPlan
    catalog: DatasetCatalogApplication

    def scan(self, kind: str, *, columns: Sequence[str] = ()) -> Any:
        """Return a Polars LazyFrame while keeping storage an internal concern."""

        try:
            import polars as pl
        except ImportError as error:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "lazy analytical scans require the kairos-platform[query] extra"
            ) from error
        normalized = normalize_kind(kind)
        if self.plan.kinds and normalized not in self.plan.kinds:
            raise LookupError(
                f"analytical kind is outside the shared read plan: {normalized}"
            )
        members = tuple(
            member
            for member in self.plan.dataset_set.members
            if normalize_kind(member.kind) == normalized
        )
        if not members:
            raise LookupError(f"Dataset Set has no analytical kind: {normalized}")
        paths = [
            str(path) for member in members for path in self.catalog.data_paths(member)
        ]
        frame = pl.scan_ndjson(paths)
        envelope = {
            "quote": "Quote",
            "trade": "Trade",
            "bar": "Bar",
            "option-greeks": "Greeks",
        }.get(normalized)
        if envelope is not None:
            frame = frame.select(pl.col(envelope).struct.unnest())
        if self.plan.start_time_unix_nanos is not None:
            frame = frame.filter(
                pl.col("observed_at_unix_nanos") >= self.plan.start_time_unix_nanos
            )
        if self.plan.end_time_unix_nanos is not None:
            frame = frame.filter(
                pl.col("observed_at_unix_nanos") <= self.plan.end_time_unix_nanos
            )
        if columns:
            frame = frame.select(*columns)
        return frame

    def point_in_time_join(
        self,
        left_kind: str,
        right_kind: str,
        *,
        by: Sequence[str] = ("instrument_id",),
        left_time: str = "observed_at_unix_nanos",
        right_time: str = "available_at_unix_nanos",
        tolerance_nanos: int | None = None,
        suffix: str = "_right",
    ) -> Any:
        """Lazily attach the latest right-side fact available at each left fact.

        This is an identity/time operation: numeric value decoding remains at
        the data owner's canonical boundary. The backward as-of direction is
        fixed so a result can never select a future-available right-side fact.
        """

        keys = tuple(value.strip() for value in by)
        if not keys or any(not value for value in keys):
            raise ValueError("point-in-time join requires non-empty identity keys")
        if not left_time.strip() or not right_time.strip():
            raise ValueError("point-in-time join time columns are required")
        if tolerance_nanos is not None and tolerance_nanos < 0:
            raise ValueError("point-in-time join tolerance cannot be negative")
        if not suffix:
            raise ValueError("point-in-time join suffix is required")

        left = self.scan(left_kind).sort([*keys, left_time])
        right = self.scan(right_kind).sort([*keys, right_time])
        return left.join_asof(
            right,
            left_on=left_time,
            right_on=right_time,
            by=list(keys),
            strategy="backward",
            tolerance=tolerance_nanos,
            suffix=suffix,
            check_sortedness=False,
        )


class ReplayStream(Iterator[Mapping[str, Any]]):
    """Deterministic event stream over exactly the snapshot's fact set."""

    def __init__(
        self,
        plan: DatasetReadPlan,
        facts: tuple[_Fact, ...],
        *,
        cursor: int = 0,
    ) -> None:
        if cursor < 0 or cursor > len(facts):
            raise ValueError("replay cursor is outside the stream")
        self.plan = plan
        self._facts = facts
        self._cursor = cursor

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def eof(self) -> bool:
        return self._cursor >= len(self._facts)

    def checkpoint(self) -> dict[str, Any]:
        return {
            "read_plan_hash": self.plan.plan_hash,
            "composition_hash": self.plan.dataset_set.composition_hash,
            "cursor": self._cursor,
            "eof": self.eof,
        }

    def resume(self, checkpoint: Mapping[str, Any]) -> "ReplayStream":
        if checkpoint.get("read_plan_hash") != self.plan.plan_hash:
            raise ValueError("replay checkpoint belongs to another read plan")
        return ReplayStream(self.plan, self._facts, cursor=int(checkpoint["cursor"]))

    def __iter__(self) -> "ReplayStream":
        return self

    def __next__(self) -> Mapping[str, Any]:
        if self.eof:
            raise StopIteration
        event = self._facts[self._cursor].event
        self._cursor += 1
        return event

    async def events(self) -> AsyncIterator[Mapping[str, Any]]:
        for event in self:
            yield event


@dataclass(frozen=True, slots=True)
class DatasetReaderApplication:
    catalog: DatasetCatalogApplication

    def plan(
        self,
        dataset_set: DatasetSetRef,
        *,
        start_time_unix_nanos: int | None = None,
        end_time_unix_nanos: int | None = None,
        kinds: tuple[str, ...] = (),
        replay_policy: Mapping[str, Any] | None = None,
    ) -> DatasetReadPlan:
        return DatasetReadPlan(
            dataset_set=dataset_set,
            start_time_unix_nanos=start_time_unix_nanos,
            end_time_unix_nanos=end_time_unix_nanos,
            kinds=tuple(normalize_kind(kind) for kind in kinds),
            replay_policy=dict(replay_policy or {}),
        )

    def snapshot(self, plan: DatasetReadPlan) -> DatasetSnapshot:
        return DatasetSnapshot(plan, self._read(plan))

    def replay(self, plan: DatasetReadPlan) -> ReplayStream:
        return ReplayStream(plan, self._read(plan))

    def analytical(self, plan: DatasetReadPlan) -> DatasetAnalyticalView:
        return DatasetAnalyticalView(plan, self.catalog)

    def materialize_replay(self, plan: DatasetReadPlan, target: Path) -> Path:
        """Compatibility materialization for the current Rust replay reader."""
        target = target.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        replay = self.replay(plan)
        temporary.write_text(
            "".join(
                json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
                for event in replay
            ),
            encoding="utf-8",
        )
        os.replace(temporary, target)
        checkpoint = replay.checkpoint()
        target.with_suffix(target.suffix + ".read-plan.json").write_text(
            json.dumps(
                {
                    "read_plan_hash": plan.plan_hash,
                    "dataset_set": plan.dataset_set.as_dict(),
                    "start_time_unix_nanos": plan.start_time_unix_nanos,
                    "end_time_unix_nanos": plan.end_time_unix_nanos,
                    "kinds": list(plan.kinds),
                    "replay_policy": dict(plan.replay_policy),
                    "checkpoint": checkpoint,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return target

    def _read(self, plan: DatasetReadPlan) -> tuple[_Fact, ...]:
        facts: list[_Fact] = []
        allowed = set(plan.kinds)
        for member in plan.dataset_set.members:
            ordinal = 0
            for path in self.catalog.data_paths(member):
                with path.open(encoding="utf-8") as source:
                    for line in source:
                        current_ordinal = ordinal
                        ordinal += 1
                        if not line.strip():
                            continue
                        event = _normalize_market_provider(json.loads(line))
                        if not isinstance(event, Mapping):
                            raise ValueError(
                                "dataset event is not an object: "
                                f"{member.identity}:{current_ordinal}"
                            )
                        kind = observation_kind(event)
                        if allowed and kind not in allowed:
                            continue
                        timestamp = event_time(event)
                        if plan.start_time_unix_nanos is not None and (
                            timestamp is None or timestamp < plan.start_time_unix_nanos
                        ):
                            continue
                        if plan.end_time_unix_nanos is not None and (
                            timestamp is None or timestamp > plan.end_time_unix_nanos
                        ):
                            continue
                        facts.append(
                            _Fact(
                                event=event,
                                owner=member.owner,
                                dataset_kind=member.kind,
                                source=member.source or "",
                                dataset_identity=member.identity,
                                ordinal=current_ordinal,
                            )
                        )
        facts.sort(key=lambda fact: fact.sort_key)
        return tuple(facts)
