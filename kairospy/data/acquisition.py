from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from .catalog import DataCatalog
from .contracts import DatasetLike, DataProductContract, DatasetRelease, SourceBinding


@dataclass(frozen=True, slots=True)
class TimeRange:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None or self.start >= self.end:
            raise ValueError("time range requires timezone-aware [start,end) with start before end")


@dataclass(frozen=True, slots=True)
class AcquisitionRequest:
    logical_key: str
    missing: tuple[TimeRange, ...]
    source: SourceBinding
    instruments: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()
    base_release_id: str | None = None


@dataclass(frozen=True, slots=True)
class AcquisitionEstimate:
    requests: int
    bytes: int | None = None
    cost_class: str = "unknown"
    instruments: int | None = None

    def __post_init__(self) -> None:
        if (
            self.requests < 0
            or self.bytes is not None and self.bytes < 0
            or self.instruments is not None and self.instruments < 0
        ):
            raise ValueError("acquisition estimates cannot be negative")


@dataclass(frozen=True, slots=True)
class AcquisitionLimits:
    maximum_requests: int = 10_000
    maximum_ranges: int = 366
    maximum_instruments: int = 10_000
    maximum_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class AcquisitionPlan:
    logical_key: str
    requested: TimeRange
    local_release_id: str | None
    covered: tuple[TimeRange, ...]
    missing: tuple[TimeRange, ...]
    candidates: tuple[SourceBinding, ...]
    selected: SourceBinding | None
    source_policy_version: str = "priority-v1"
    connector_available: bool = False
    estimate: AcquisitionEstimate | None = None

    @property
    def complete(self) -> bool:
        return not self.missing

    @property
    def executable(self) -> bool:
        return self.complete or self.selected is not None and self.connector_available


class ProviderConnector(Protocol):
    provider: str

    def supports(self, logical_key: str) -> bool:
        """Return whether this connector can publish the requested product."""

    def acquire(self, request: AcquisitionRequest) -> DatasetRelease:
        """Archive Source, canonicalize, validate and return one immutable release."""

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        """Return a conservative request/size estimate without network access."""


class ProviderRegistry:
    def __init__(self) -> None:
        self._connectors: dict[str, list[ProviderConnector]] = {}
        self._specs: dict[str, DataProductContract] = {}

    def register(self, connector: ProviderConnector, specs: tuple[DataProductContract, ...] = ()) -> None:
        provider = connector.provider.strip()
        if not provider:
            raise ValueError("provider connector must declare a provider")
        values = self._connectors.setdefault(provider, [])
        if connector not in values:
            values.append(connector)
        for spec in specs:
            key = str(spec.key)
            if not connector.supports(key):
                raise ValueError(f"connector {provider!r} does not support declared data product contract {key!r}")
            if not any(source.provider == provider for source in spec.product.sources):
                raise ValueError(f"data product contract {key!r} does not declare provider {provider!r}")
            previous = self._specs.get(key)
            if previous is not None and previous != spec:
                raise ValueError(f"conflicting provider data product contract registration: {key}")
            self._specs[key] = spec

    def get(self, provider: str, logical_key: str) -> ProviderConnector:
        matches = [item for item in self._connectors.get(provider, ()) if item.supports(logical_key)]
        if not matches:
            raise RuntimeError(f"no acquisition connector registered for provider {provider!r} and product {logical_key!r}")
        if len(matches) > 1:
            raise RuntimeError(f"multiple acquisition connectors claim provider {provider!r} and product {logical_key!r}")
        return matches[0]

    def available(self, provider: str, logical_key: str) -> bool:
        return any(item.supports(logical_key) for item in self._connectors.get(provider, ()))

    def product_spec(self, logical_key: str) -> DataProductContract:
        try:
            return self._specs[logical_key]
        except KeyError as error:
            raise KeyError(f"provider registry has no data product contract for {logical_key!r}") from error


class CoveragePlanner:
    def __init__(self, catalog: DataCatalog, metadata_loader) -> None:
        self.catalog, self.metadata_loader = catalog, metadata_loader

    def plan(self, dataset: DatasetLike, start: datetime, end: datetime, *, provider: str | None = None,
             venue: str | None = None) -> AcquisitionPlan:
        requested = TimeRange(start, end)
        product = self.catalog.product(dataset)
        local_release = None
        covered: tuple[TimeRange, ...] = ()
        try:
            local_release = self.catalog.release(product, provider=provider, venue=venue)
        except KeyError:
            pass
        if local_release is not None:
            covered = _coverage_ranges(self.metadata_loader(local_release.release_id), requested)
        missing = _subtract(requested, covered)
        candidates = tuple(sorted((item for item in product.sources
                                   if (provider is None or item.provider == provider)
                                   and (venue is None or item.venue == venue)),
                                  key=lambda item: item.priority, reverse=True))
        selected = candidates[0] if candidates else None
        return AcquisitionPlan(str(product.key), requested, local_release.release_id if local_release else None,
                               covered, missing, candidates, selected, product.source_policy_version)


def _coverage_ranges(metadata: dict[str, object], requested: TimeRange) -> tuple[TimeRange, ...]:
    document = metadata.get("coverage", {})
    raw = document
    if isinstance(raw, dict) and isinstance(raw.get("coverage"), dict):
        raw = raw["coverage"]
    if isinstance(raw, dict):
        observed = raw.get("observed_window")
        if isinstance(observed, dict):
            raw = observed
    if not isinstance(raw, dict):
        return ()
    start = raw.get("start") or raw.get("minimum_event_time")
    end = raw.get("end") or raw.get("maximum_available_time") or raw.get("maximum_event_time")
    if start is None or end is None:
        return ()
    end_value = _datetime(end)
    if "end" not in raw and ("maximum_available_time" in raw or "maximum_event_time" in raw):
        end_value += timedelta(microseconds=1)
    value = TimeRange(_datetime(start), end_value)
    gaps = []
    if isinstance(document, dict):
        for item in document.get("missing_ranges", []):
            if isinstance(item, dict) and item.get("start") is not None and item.get("end") is not None:
                gap_start, gap_end = _datetime(item["start"]), _datetime(item["end"])
                if gap_start < gap_end:
                    gaps.append(TimeRange(gap_start, gap_end))
    covered = _exclude(value, tuple(gaps))
    result = []
    for item in covered:
        intersection_start, intersection_end = max(item.start, requested.start), min(item.end, requested.end)
        if intersection_start < intersection_end:
            result.append(TimeRange(intersection_start, intersection_end))
    return tuple(result)


def _exclude(base: TimeRange, excluded: tuple[TimeRange, ...]) -> tuple[TimeRange, ...]:
    cursor, included = base.start, []
    for item in sorted(excluded, key=lambda value: value.start):
        if item.end <= cursor or item.start >= base.end:
            continue
        if item.start > cursor:
            included.append(TimeRange(cursor, min(item.start, base.end)))
        cursor = max(cursor, item.end)
    if cursor < base.end:
        included.append(TimeRange(cursor, base.end))
    return tuple(included)


def _subtract(requested: TimeRange, covered: tuple[TimeRange, ...]) -> tuple[TimeRange, ...]:
    cursor, missing = requested.start, []
    for item in sorted(covered, key=lambda value: value.start):
        if item.end <= cursor or item.start >= requested.end:
            continue
        if item.start > cursor:
            missing.append(TimeRange(cursor, min(item.start, requested.end)))
        cursor = max(cursor, item.end)
        if cursor >= requested.end:
            break
    if cursor < requested.end:
        missing.append(TimeRange(cursor, requested.end))
    return tuple(missing)


def _datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("coverage timestamps must be timezone-aware")
    return parsed
