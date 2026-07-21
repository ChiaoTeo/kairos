from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from .catalog import DataCatalog
from .builders import DataProductBuilder
from .contracts import DatasetLike, DataProductContract, DatasetRelease
from .acquisition_primitives import (
    AcquisitionEstimate, AcquisitionLimits, AcquisitionPlan, AcquisitionRequest, TimeRange,
)


class ProviderConnector(DataProductBuilder, Protocol):
    """Legacy name for a DataProductBuilder.

    The object registered here builds governed Data datasets from provider
    sources. External provider access belongs in ``kairospy.connectors``.
    """

    provider: str

    def supports(self, logical_key: str) -> bool:
        """Return whether this builder can publish the requested Data Product."""

    def acquire(self, request: AcquisitionRequest) -> DatasetRelease:
        """Archive Source, canonicalize, validate and return an internal dataset revision."""

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        """Return a conservative request/size estimate without network access."""


class ProviderRegistry:
    def __init__(self) -> None:
        self._connectors: dict[str, list[ProviderConnector]] = {}
        self._specs: dict[str, DataProductContract] = {}

    def register(self, connector: ProviderConnector, specs: tuple[DataProductContract, ...] = ()) -> None:
        provider = connector.provider.strip()
        if not provider:
            raise ValueError("Data Product builder must declare a provider")
        values = self._connectors.setdefault(provider, [])
        if connector not in values:
            values.append(connector)
        for spec in specs:
            key = str(spec.key)
            if not connector.supports(key):
                raise ValueError(f"Data Product builder for provider {provider!r} does not support declared Data Product {key!r}")
            if not any(source.provider == provider for source in spec.product.sources):
                raise ValueError(f"data product contract {key!r} does not declare provider {provider!r}")
            previous = self._specs.get(key)
            if previous is not None and previous != spec:
                raise ValueError(f"conflicting provider data product contract registration: {key}")
            self._specs[key] = spec

    def get(self, provider: str, logical_key: str) -> ProviderConnector:
        matches = [item for item in self._connectors.get(provider, ()) if item.supports(logical_key)]
        if not matches:
            raise RuntimeError(
                f"no Data Product builder registered for provider {provider!r} and Data Product {logical_key!r}; "
                f"run `kairospy providers doctor {provider}` or `kairospy data products doctor {logical_key}`"
            )
        if len(matches) > 1:
            raise RuntimeError(f"multiple Data Product builders claim provider {provider!r} and Data Product {logical_key!r}")
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
