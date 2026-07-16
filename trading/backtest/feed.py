from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path

from trading.catalog.repository import definition_from_primitive
from trading.domain.identity import InstrumentId
from trading.domain.instrument import InstrumentDefinition
from trading.research.snapshot import DataQualityIssue, InstrumentSnapshot
from trading.storage.codec import from_primitive, to_primitive


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


class HistoricalFeed:
    def __init__(self, dataset: HistoricalDataset) -> None:
        self.dataset = dataset

    def between(self, start: datetime, end: datetime):
        for market_slice in self.dataset.slices:
            if start <= market_slice.timestamp < end:
                yield market_slice


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
) -> DatasetManifest:
    total_instruments = sum(len(item.instruments) for item in slices)
    quote_count = sum(sum(snapshot.quote is not None for snapshot in item.instruments) for item in slices)
    greek_count = sum(sum(snapshot.greeks is not None for snapshot in item.instruments) for item in slices)
    stale_count = sum(sum(issue.code.startswith("stale") for issue in item.quality_issues) for item in slices)
    expected = sum(len(item.instrument_universe) for item in slices)
    primitive = {"slices": to_primitive(slices), "contracts": to_primitive(contracts), "definitions": to_primitive(definitions)}
    content_hash = sha256(json.dumps(primitive, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    denominator = Decimal(total_instruments or 1)
    return DatasetManifest(
        1, dataset_id, slices[0].timestamp, slices[-1].timestamp + timedelta(seconds=sampling_seconds), sampling_seconds,
        len({item.timestamp.date() for item in slices}), len(slices),
        Decimal(total_instruments) / Decimal(expected or 1), Decimal(quote_count) / denominator,
        Decimal(greek_count) / denominator, Decimal(stale_count) / denominator,
        source, market_data_type, code_version, content_hash, split, synthetic,
    )


class DatasetRepository:
    def __init__(self, root: str | Path = "data/datasets") -> None:
        self.root = Path(root)

    def save(self, dataset: HistoricalDataset) -> Path:
        directory = self.root / dataset.manifest.dataset_id
        directory.mkdir(parents=True, exist_ok=True)
        payload = {"manifest": to_primitive(dataset.manifest), "contracts": to_primitive(dataset.contracts), "definitions": to_primitive(dataset.definitions), "slices": to_primitive(dataset.slices)}
        temporary = directory / "dataset.json.tmp"
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(directory / "dataset.json")
        return directory

    def load(self, dataset_id: str) -> HistoricalDataset:
        value = json.loads((self.root / dataset_id / "dataset.json").read_text(encoding="utf-8"))
        manifest = from_primitive(value["manifest"], DatasetManifest)
        if manifest.schema_version != 1:
            raise ValueError(f"unsupported dataset schema version: {manifest.schema_version}")
        contracts = tuple(from_primitive(item, ContractMetadata) for item in value["contracts"])
        definitions = tuple(definition_from_primitive(item) for item in value["definitions"])
        slices = tuple(from_primitive(item, MarketSlice) for item in value["slices"])
        dataset = HistoricalDataset(manifest, slices, contracts, definitions)
        rebuilt = build_manifest(
            manifest.dataset_id, slices, contracts, definitions, sampling_seconds=manifest.sampling_seconds,
            source=manifest.source, market_data_type=manifest.market_data_type, code_version=manifest.code_version,
            split=manifest.split, synthetic=manifest.synthetic,
        )
        if rebuilt.content_hash != manifest.content_hash:
            raise ValueError("dataset content hash mismatch")
        return dataset
