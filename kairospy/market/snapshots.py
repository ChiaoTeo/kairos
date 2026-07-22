from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum, StrEnum
from hashlib import sha256
import json
from typing import Protocol
from uuid import UUID

from kairospy.identity import InstrumentId
from kairospy.reference.catalog import ReferenceCatalog
from kairospy.reference.contracts import (
    EconomicProduct,
    InstrumentDefinition,
    InstrumentReference,
    SettlementTermsDefinition,
)

from .slices import DataQualityIssue, InstrumentSnapshot


class SettlementType(StrEnum):
    AM = "am"
    PM = "pm"


@dataclass(frozen=True, slots=True)
class InstrumentLifecycleSnapshot:
    instrument_id: InstrumentId
    last_trade_at: datetime
    settlement_at: datetime
    settlement_type: SettlementType
    official_settlement: Decimal | None = None
    settlement_confirmed: bool = False
    metadata_source: str = "unknown"


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    timestamp: datetime
    instruments: tuple[InstrumentSnapshot, ...]
    reference_prices: tuple[tuple[InstrumentId, Decimal], ...] = ()
    quality_issues: tuple[DataQualityIssue, ...] = ()
    snapshot_span_seconds: Decimal = Decimal("0")
    sequence: int = 0
    available_instruments: tuple[InstrumentId, ...] = ()
    available_time: datetime | None = None
    freshness_seconds: Decimal | None = None
    data_binding: str = "unknown"
    event_window: tuple[datetime, datetime] | None = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("market snapshot timestamp must be timezone-aware")
        if self.available_time is None:
            object.__setattr__(self, "available_time", self.timestamp)
        if self.available_time is not None and self.available_time.tzinfo is None:
            raise ValueError("market snapshot available_time must be timezone-aware")
        if self.event_window is None:
            start = self.timestamp - timedelta(seconds=float(self.snapshot_span_seconds))
            object.__setattr__(self, "event_window", (start, self.timestamp))
        if self.event_window is not None:
            start, end = self.event_window
            if start.tzinfo is None or end.tzinfo is None:
                raise ValueError("market snapshot event_window timestamps must be timezone-aware")
            if end < start:
                raise ValueError("market snapshot event_window end cannot precede start")
            if self.available_time is not None and self.available_time < end:
                raise ValueError("market snapshot available_time cannot precede event_window end")
        if self.freshness_seconds is None and self.available_time is not None:
            object.__setattr__(self, "freshness_seconds", Decimal(str((self.available_time - self.timestamp).total_seconds())))
        if self.freshness_seconds is not None and self.freshness_seconds < 0:
            raise ValueError("market snapshot freshness_seconds cannot be negative")
        if not self.data_binding.strip():
            raise ValueError("market snapshot data_binding cannot be empty")
        if any(price <= 0 for _, price in self.reference_prices):
            raise ValueError("reference prices must be positive")
        if self.available_instruments and len(set(self.available_instruments)) != len(self.available_instruments):
            raise ValueError("available instrument universe cannot contain duplicates")

    @property
    def instrument_universe(self) -> tuple[InstrumentId, ...]:
        return self.available_instruments or tuple(item.instrument_id for item in self.instruments)


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    schema_version: int
    dataset_id: str
    start: datetime
    end: datetime
    sampling_seconds: int
    trading_days: int
    slice_count: int
    contract_coverage: Decimal
    quote_coverage: Decimal
    greeks_coverage: Decimal
    stale_rate: Decimal
    source: str
    market_data_type: str
    code_version: str
    content_hash: str
    split: str
    synthetic: bool


@dataclass(frozen=True, slots=True)
class MarketReplayDataset:
    manifest: DatasetManifest
    slices: tuple[MarketSnapshot, ...]
    contracts: tuple[InstrumentLifecycleSnapshot, ...]
    definitions: tuple[InstrumentDefinition, ...]
    products: tuple[EconomicProduct, ...] = ()
    references: tuple[InstrumentReference, ...] = ()
    settlements: tuple[SettlementTermsDefinition, ...] = ()

    def __post_init__(self) -> None:
        if not self.slices:
            raise ValueError("dataset must contain market snapshots")
        ordered = sorted(self.slices, key=lambda item: (item.timestamp, item.sequence))
        if list(self.slices) != ordered:
            raise ValueError("market snapshots must be monotonically ordered")
        if any(a.timestamp == b.timestamp and a.sequence >= b.sequence for a, b in zip(self.slices, self.slices[1:])):
            raise ValueError("sequence must increase for equal timestamps")
        known = {item.instrument_id for item in self.definitions}
        referenced = {item.instrument_id for market in self.slices for item in market.instruments}
        referenced.update(instrument_id for market in self.slices for instrument_id in market.instrument_universe)
        if not referenced <= known:
            raise ValueError("dataset market slices reference unknown instruments")
        for market in self.slices:
            if not {item.instrument_id for item in market.instruments} <= set(market.instrument_universe):
                raise ValueError("market snapshot contains instrument outside its point-in-time universe")

    def reference_catalog(self) -> ReferenceCatalog:
        catalog = ReferenceCatalog()
        for product in self.products:
            catalog.products.add(product)
        for definition in self.definitions:
            catalog.instruments.add(definition)
        for reference in self.references:
            catalog.add_reference(reference)
        for settlement in self.settlements:
            catalog.settlements.add(settlement)
        return catalog


HistoricalDataset = MarketReplayDataset


class MarketSnapshotReplayFeed:
    def __init__(self, dataset: MarketReplayDataset) -> None:
        self.dataset = dataset

    def between(self, start: datetime, end: datetime):
        for market_snapshot in self.dataset.slices:
            if start <= market_snapshot.timestamp < end:
                if market_snapshot.data_binding == "unknown":
                    yield replace(market_snapshot, data_binding=self.dataset.manifest.dataset_id)
                else:
                    yield market_snapshot


class MarketSnapshotFeed(Protocol):
    dataset: MarketReplayDataset

    def between(self, start: datetime, end: datetime): ...


def build_manifest(
    dataset_id: str,
    slices: tuple[MarketSnapshot, ...],
    contracts: tuple[InstrumentLifecycleSnapshot, ...],
    definitions: tuple[InstrumentDefinition, ...],
    *,
    sampling_seconds: int,
    source: str,
    market_data_type: str,
    code_version: str,
    split: str,
    synthetic: bool,
    products: tuple[EconomicProduct, ...] = (),
    references: tuple[InstrumentReference, ...] = (),
    settlements: tuple[SettlementTermsDefinition, ...] = (),
) -> DatasetManifest:
    total_instruments = sum(len(item.instruments) for item in slices)
    quote_count = sum(sum(snapshot.quote is not None for snapshot in item.instruments) for item in slices)
    greek_count = sum(sum(snapshot.greeks is not None for snapshot in item.instruments) for item in slices)
    stale_count = sum(sum(issue.code.startswith("stale") for issue in item.quality_issues) for item in slices)
    expected = sum(len(item.instrument_universe) for item in slices)
    primitive = {
        "slices": _stable_primitive(slices),
        "contracts": _stable_primitive(contracts),
        "definitions": [_instrument_to_hash_primitive(item) for item in definitions],
        "products": _stable_primitive(products),
        "references": _stable_primitive(references),
        "settlements": _stable_primitive(settlements),
    }
    content_hash = sha256(json.dumps(primitive, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    denominator = Decimal(total_instruments or 1)
    return DatasetManifest(
        1,
        dataset_id,
        slices[0].timestamp,
        slices[-1].timestamp + timedelta(seconds=sampling_seconds),
        sampling_seconds,
        len({item.timestamp.date() for item in slices}),
        len(slices),
        Decimal(total_instruments) / Decimal(expected or 1),
        Decimal(quote_count) / denominator,
        Decimal(greek_count) / denominator,
        Decimal(stale_count) / denominator,
        source,
        market_data_type,
        code_version,
        content_hash,
        split,
        synthetic,
    )


def _instrument_to_hash_primitive(item: InstrumentDefinition) -> dict[str, object]:
    value = _stable_primitive(item)
    if not isinstance(value, dict):
        raise TypeError("instrument definition must encode to a JSON object")
    value["contract_spec_type"] = type(item.contract_spec).__name__
    return value


def _stable_primitive(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, time):
        return {"$time": value.isoformat()}
    if isinstance(value, UUID):
        return {"$uuid": str(value)}
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: _stable_primitive(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _stable_primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_stable_primitive(item) for item in value]
    raise TypeError(f"cannot serialize {type(value).__name__}")
