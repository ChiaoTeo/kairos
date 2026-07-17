from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
from typing import Protocol

from trading.domain.identity import InstrumentId
from trading.reference.catalog import ReferenceCatalog
from trading.reference.models import EconomicProduct, InstrumentDefinition, InstrumentReference, SettlementTermsDefinition
from trading.reference.repository import instrument_to_primitive
from trading.research.snapshot import DataQualityIssue, InstrumentSnapshot
from trading.storage.codec import to_primitive


class SettlementType(StrEnum):
    AM = "am"
    PM = "pm"


@dataclass(frozen=True, slots=True)
class ContractMetadata:
    instrument_id: InstrumentId
    last_trade_at: datetime
    settlement_at: datetime
    settlement_type: SettlementType
    official_settlement: Decimal | None = None
    settlement_confirmed: bool = False
    metadata_source: str = "unknown"


@dataclass(frozen=True, slots=True)
class MarketSlice:
    timestamp: datetime
    instruments: tuple[InstrumentSnapshot, ...]
    reference_prices: tuple[tuple[InstrumentId, Decimal], ...] = ()
    quality_issues: tuple[DataQualityIssue, ...] = ()
    snapshot_span_seconds: Decimal = Decimal("0")
    sequence: int = 0
    available_instruments: tuple[InstrumentId, ...] = ()

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("market slice timestamp must be timezone-aware")
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
class HistoricalDataset:
    manifest: DatasetManifest
    slices: tuple[MarketSlice, ...]
    contracts: tuple[ContractMetadata, ...]
    definitions: tuple[InstrumentDefinition, ...]
    products: tuple[EconomicProduct, ...] = ()
    references: tuple[InstrumentReference, ...] = ()
    settlements: tuple[SettlementTermsDefinition, ...] = ()

    def __post_init__(self) -> None:
        if not self.slices:
            raise ValueError("dataset must contain market slices")
        ordered = sorted(self.slices, key=lambda item: (item.timestamp, item.sequence))
        if list(self.slices) != ordered:
            raise ValueError("market slices must be monotonically ordered")
        if any(a.timestamp == b.timestamp and a.sequence >= b.sequence for a, b in zip(self.slices, self.slices[1:])):
            raise ValueError("sequence must increase for equal timestamps")
        known = {item.instrument_id for item in self.definitions}
        referenced = {item.instrument_id for market in self.slices for item in market.instruments}
        referenced.update(instrument_id for market in self.slices for instrument_id in market.instrument_universe)
        if not referenced <= known:
            raise ValueError("dataset market slices reference unknown instruments")
        for market in self.slices:
            if not {item.instrument_id for item in market.instruments} <= set(market.instrument_universe):
                raise ValueError("market slice contains instrument outside its point-in-time universe")

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


class HistoricalFeed:
    def __init__(self, dataset: HistoricalDataset) -> None:
        self.dataset = dataset

    def between(self, start: datetime, end: datetime):
        for market_slice in self.dataset.slices:
            if start <= market_slice.timestamp < end:
                yield market_slice


class MarketSliceFeed(Protocol):
    dataset: HistoricalDataset

    def between(self, start: datetime, end: datetime): ...


def build_manifest(
    dataset_id: str,
    slices: tuple[MarketSlice, ...],
    contracts: tuple[ContractMetadata, ...],
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
    primitive = {"slices": to_primitive(slices), "contracts": to_primitive(contracts),
                 "definitions": [instrument_to_primitive(item) for item in definitions],
                 "products": to_primitive(products), "references": to_primitive(references),
                 "settlements": to_primitive(settlements)}
    content_hash = sha256(json.dumps(primitive, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    denominator = Decimal(total_instruments or 1)
    return DatasetManifest(
        1, dataset_id, slices[0].timestamp, slices[-1].timestamp + timedelta(seconds=sampling_seconds), sampling_seconds,
        len({item.timestamp.date() for item in slices}), len(slices),
        Decimal(total_instruments) / Decimal(expected or 1), Decimal(quote_count) / denominator,
        Decimal(greek_count) / denominator, Decimal(stale_count) / denominator,
        source, market_data_type, code_version, content_hash, split, synthetic,
    )
