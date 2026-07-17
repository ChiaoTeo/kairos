from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from math import exp
from pathlib import Path

from trading import __version__
from trading.backtest.feed import ContractMetadata, HistoricalDataset, MarketSlice, SettlementType, build_manifest
from trading.data.market_slice_storage import MarketSliceStorageDriver
from trading.reference import ReferenceCatalog, ReferenceCatalogRepository, ReferenceRole
from trading.reference.access import contract_spec, product_type
from trading.research.snapshot import build_reference_evidence
from trading.data.catalog import DataCatalog
from trading.data.models import (
    DatasetKey, DatasetLayer, DatasetProduct, DatasetRelease, DatasetStatus, DatasetStorageKind, QualityLevel,
)
from trading.domain.market_data import Quote
from trading.domain.product import ListedOptionSpec, OptionRight, ProductType, SettlementSession
from trading.market_data import MarketEventType, ParquetMarketEventRepository
from trading.research.data_store import MarketSliceCollectionPublisher
from trading.research.snapshot import DataQualityIssue, InstrumentSnapshot
from trading.storage.data_lake import sha256_bytes, write_json


class MassiveCuratedSliceBuilder:
    def __init__(self, lake_root: str | Path = "data", *,
                 reference_catalog_path: str | Path | None = None, dataset_root: str | Path | None = None) -> None:
        self.lake_root = Path(lake_root)
        reference_path = Path(reference_catalog_path) if reference_catalog_path is not None else self.lake_root / "reference" / "catalog.json"
        if not reference_path.exists():
            raise FileNotFoundError(f"reference catalog is missing: {reference_path}")
        self.catalog = ReferenceCatalogRepository(reference_path).load()
        self.events = ParquetMarketEventRepository(self.lake_root / "canonical" / "market")
        self.repository = MarketSliceStorageDriver(dataset_root if dataset_root is not None else self.lake_root / "curated")

    def build(self, source_dataset_id: str, output_dataset_id: str, start: datetime, end: datetime, *,
              sampling_seconds: int = 60, max_quote_age_seconds: int = 300, split: str = "development",
              risk_free_rate: Decimal = Decimal("0")) -> HistoricalDataset:
        if start.tzinfo is None or end.tzinfo is None or not start < end:
            raise ValueError("curated slice builder requires timezone-aware [start,end)")
        if sampling_seconds <= 0 or max_quote_age_seconds <= 0:
            raise ValueError("sampling and quote age must be positive")
        quote_ids = set(self.events.distinct_instruments(source_dataset_id, start, end, event_types=(MarketEventType.QUOTE,)))
        bar_ids = set(self.events.distinct_instruments(source_dataset_id, start, end, event_types=(MarketEventType.BAR,)))
        definitions = self.catalog.instruments.values(start)
        options = tuple(item for item in definitions if product_type(item) is ProductType.LISTED_OPTION and item.instrument_id in quote_ids)
        if not options:
            raise ValueError("catalog contains no active listed options")
        option_ids = {item.instrument_id for item in options}
        underlying_ids = {
            reference.target.instrument_id for item in options
            for reference in self.catalog.references(item.instrument_id, ReferenceRole.PRICING_UNDERLYING, start)
            if reference.target.instrument_id is not None
        }
        relevant_ids = (*option_ids, *underlying_ids)
        events = iter(self.events.scan(source_dataset_id, start, end, instruments=relevant_ids,
                                       event_types=(MarketEventType.QUOTE, MarketEventType.BAR), view="raw-as-received"))
        pending = next(events, None)
        latest_quotes, official_references, slices = {}, {}, []
        cursor, sequence = start, 0
        while cursor < end:
            while pending is not None and pending.available_time <= cursor:
                if pending.record_type is MarketEventType.QUOTE:
                    latest_quotes[pending.instrument_id] = pending
                elif pending.record_type is MarketEventType.BAR and pending.payload.get("close") is not None:
                    official_references[pending.instrument_id] = Decimal(str(pending.payload["close"]))
                pending = next(events, None)
            snapshots, issues = [], []
            for definition in options:
                event = latest_quotes.get(definition.instrument_id)
                if event is None:
                    snapshots.append(InstrumentSnapshot(definition.instrument_id, None, None, None, None, None, None))
                    issues.append(DataQualityIssue("missing_quote", "Massive quote is not available at slice time", "error", definition.instrument_id))
                    continue
                age = (cursor - event.available_time).total_seconds()
                if age > max_quote_age_seconds:
                    issues.append(DataQualityIssue("stale_quote", f"Massive quote age {age}s exceeds {max_quote_age_seconds}s", "error", definition.instrument_id))
                quote = Quote(definition.instrument_id, _decimal(event.payload.get("bid")), _decimal(event.payload.get("ask")),
                              _decimal(event.payload.get("bid_size")), _decimal(event.payload.get("ask_size")), event.event_time)
                snapshots.append(InstrumentSnapshot(definition.instrument_id, quote, event.available_time, None, None, None, None))
            slice_references = dict(official_references)
            for underlying_id in underlying_ids:
                if underlying_id not in slice_references:
                    forward, pair_count = _synthetic_forward(
                        cursor, underlying_id, options, latest_quotes,
                        max_quote_age_seconds=max_quote_age_seconds, risk_free_rate=risk_free_rate,
                    )
                    if forward is None:
                        issues.append(DataQualityIssue(
                            "missing_underlying",
                            "neither a completed Massive underlying bar nor a fresh call/put pair is available",
                            "error", underlying_id,
                        ))
                    else:
                        slice_references[underlying_id] = forward
                        issues.append(DataQualityIssue(
                            "synthetic_forward",
                            f"reference derived point-in-time from {pair_count} call/put pair(s) by put-call parity; risk_free_rate={risk_free_rate}",
                            "info", underlying_id,
                        ))
            slices.append(MarketSlice(cursor, tuple(snapshots), tuple(sorted(slice_references.items(), key=lambda item: item[0].value)),
                                      tuple(issues), Decimal("0"), sequence, tuple(item.instrument_id for item in options)))
            cursor += timedelta(seconds=sampling_seconds); sequence += 1
        contracts = tuple(_contract(item) for item in options)
        used_definitions = tuple(item for item in definitions if item.instrument_id in option_ids | underlying_ids)
        used_product_ids = {item.product_id for item in used_definitions}
        used_instrument_ids = {item.instrument_id for item in used_definitions}
        used_products = tuple(item for item in self.catalog.products.values() if item.product_id in used_product_ids)
        used_references = tuple(item for item in self.catalog.all_references() if item.source_instrument_id in used_instrument_ids)
        used_settlements = tuple(
            self.catalog.settlements.get(item.settlement_terms_id, start)
            for item in used_definitions if item.settlement_terms_id is not None
        )
        evidence = build_reference_evidence(self.catalog, tuple(item.instrument_id for item in used_definitions), start)
        reference_suffix = f";reference_hash={evidence.content_hash}"
        manifest = build_manifest(output_dataset_id, tuple(slices), contracts, used_definitions,
                                  sampling_seconds=sampling_seconds,
                                  source=f"massive.canonical:{source_dataset_id};reference=official_or_put_call_parity;r={risk_free_rate}{reference_suffix}",
                                  market_data_type="historical_quotes", code_version=__version__, split=split, synthetic=False,
                                  products=used_products, references=used_references, settlements=used_settlements)
        dataset = HistoricalDataset(
            manifest, tuple(slices), contracts, used_definitions, used_products, used_references, used_settlements,
        )
        MarketSliceCollectionPublisher(self.repository).save_session(dataset, append=False)
        self._publish(source_dataset_id, dataset)
        return dataset

    def _publish(self, source_dataset_id: str, dataset: HistoricalDataset) -> None:
        directory = self.repository.root / dataset.manifest.dataset_id
        logical_key = DatasetKey(f"curated.market_slices.options.us.{_dataset_underlying(source_dataset_id)}")
        error_count = sum(issue.severity == "error" for item in dataset.slices for issue in item.quality_issues)
        checks = (
            {"name": "non_empty", "passed": dataset.manifest.slice_count > 0, "value": dataset.manifest.slice_count},
            {"name": "contract_coverage", "passed": dataset.manifest.contract_coverage >= Decimal("0.95"),
             "value": str(dataset.manifest.contract_coverage), "minimum": "0.95"},
            {"name": "quote_coverage", "passed": dataset.manifest.quote_coverage >= Decimal("0.95"),
             "value": str(dataset.manifest.quote_coverage), "minimum": "0.95"},
            {"name": "stale_rate", "passed": dataset.manifest.stale_rate <= Decimal("0.05"),
             "value": str(dataset.manifest.stale_rate), "maximum": "0.05"},
            {"name": "quality_errors", "passed": error_count == 0, "value": error_count, "maximum": 0},
        )
        passed = all(item["passed"] for item in checks)
        catalog = DataCatalog(self.lake_root)
        source = catalog.release(source_dataset_id)
        if source.content_hash is None:
            raise ValueError("curated dataset requires a frozen source release hash")
        relative_path = str(directory.relative_to(self.lake_root))
        parquet = directory / "slices.parquet"
        files = [{"path": parquet.name, "bytes": parquet.stat().st_size,
                  "sha256": sha256_bytes(parquet.read_bytes())}] if parquet.exists() else []
        write_json(directory / "schema.json", {
            "schema_id": "historical_dataset.v2", "schema_version": 2,
            "primary_key": ["timestamp", "sequence"], "primary_time": "timestamp",
            "time_semantics": {"timestamp": "point-in-time market availability"},
        })
        write_json(directory / "lineage.json", {
            "lineage_version": 2, "dataset_id": dataset.manifest.dataset_id,
            "producer": {"name": type(self).__name__, "transform": "point_in_time_market_slices", "version": 2},
            "inputs": [{"release_id": source.release_id, "content_hash": source.content_hash}],
            "parameters": {"sampling_seconds": dataset.manifest.sampling_seconds}, "point_in_time_safe": True,
        })
        write_json(directory / "coverage.json", {
            "dataset_id": dataset.manifest.dataset_id, "timezone": "UTC", "boundary": "[start,end)",
            "coverage": {"start": dataset.manifest.start.isoformat(), "end": dataset.manifest.end.isoformat(),
                         "slices": dataset.manifest.slice_count, "contract_coverage": str(dataset.manifest.contract_coverage),
                         "quote_coverage": str(dataset.manifest.quote_coverage), "stale_rate": str(dataset.manifest.stale_rate)},
        })
        write_json(directory / "quality.json", {"quality_schema_version": 1,
            "dataset_id": dataset.manifest.dataset_id, "passed": passed, "checks": checks})
        write_json(directory / "manifest.json", {"manifest_version": 2, "dataset_id": dataset.manifest.dataset_id,
            "generated_at": dataset.manifest.end.isoformat(), "files": files, "rows": dataset.manifest.slice_count,
            "dataset_sha256": dataset.manifest.content_hash})
        write_json(directory / "capabilities.json", {"capability_schema_version": 2,
            "dataset_id": dataset.manifest.dataset_id, "point_in_time_universe": True,
            "synchronous_quotes": True, "top_of_book": True, "maximum_validation_level": 3 if passed else 1})
        product = DatasetProduct(logical_key, f"Point-in-time {_dataset_underlying(source_dataset_id).upper()} option slices",
            DatasetLayer.CURATED, dimensions={"asset_class": "option", "region": "us",
            "underlying": _dataset_underlying(source_dataset_id).upper(), "frequency": f"{dataset.manifest.sampling_seconds}s"},
            primary_time="timestamp")
        status = DatasetStatus.APPROVED_FOR_BACKTEST if passed else DatasetStatus.VALIDATED
        quality_level = QualityLevel.BACKTEST if passed else QualityLevel.INTEGRITY
        release = DatasetRelease(dataset.manifest.dataset_id, logical_key, dataset.manifest.end.isoformat(),
            "historical_dataset.v2", "2", "point_in_time_market_slices", "2", relative_path, "parquet",
            dataset.manifest.content_hash, "internal", source.venue,
            (f"{logical_key}@latest-validated",) if passed else (), status, quality_level,
            dataset.manifest.end.isoformat(), DatasetStorageKind.MARKET_SLICES, "1")
        write_json(directory / "release.json", {"release_id": release.release_id, "logical_key": str(logical_key),
            "content_hash": release.content_hash, "status": release.status.value, "quality_level": release.quality_level.value})
        write_json(directory / "usage.json", {"primary_time": "timestamp", "default_view": "raw-as-received",
            "known_limitations": ["sampled_market_slices"]})
        catalog.register_product(product, enrich=True); catalog.register_release(release); catalog.save()


def _contract(definition) -> ContractMetadata:
    spec = contract_spec(definition)
    if not isinstance(spec, ListedOptionSpec):
        raise TypeError("contract metadata requires ListedOptionSpec")
    return ContractMetadata(definition.instrument_id, spec.last_trade_at, spec.expiry,
                            SettlementType.AM if spec.settlement_session is SettlementSession.AM else SettlementType.PM,
                            None, False, "massive.options-contracts")


def _decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _dataset_underlying(dataset_id: str) -> str:
    parts = dataset_id.lower().split(".")
    return parts[3] if len(parts) > 3 and parts[:3] == ["options", "us", "massive"] else "unknown"


def _synthetic_forward(
    as_of: datetime,
    underlying_id,
    definitions,
    latest_quotes,
    *,
    max_quote_age_seconds: int,
    risk_free_rate: Decimal,
) -> tuple[Decimal | None, int]:
    pairs: dict[tuple[datetime, Decimal], dict[OptionRight, tuple[Decimal, datetime]]] = {}
    for definition in definitions:
        spec = contract_spec(definition)
        if not isinstance(spec, ListedOptionSpec) or spec.underlying != underlying_id or spec.expiry <= as_of:
            continue
        event = latest_quotes.get(definition.instrument_id)
        if event is None or event.available_time > as_of:
            continue
        if (as_of - event.available_time).total_seconds() > max_quote_age_seconds:
            continue
        bid, ask = _decimal(event.payload.get("bid")), _decimal(event.payload.get("ask"))
        if bid is None or ask is None or bid < 0 or ask < bid:
            continue
        pairs.setdefault((spec.expiry, spec.strike), {})[spec.right] = ((bid + ask) / 2, event.available_time)
    candidates = []
    seconds_per_year = Decimal("31557600")
    for (expiry, strike), sides in pairs.items():
        if OptionRight.CALL not in sides or OptionRight.PUT not in sides:
            continue
        call_mid, _ = sides[OptionRight.CALL]
        put_mid, _ = sides[OptionRight.PUT]
        maturity = Decimal(str((expiry - as_of).total_seconds())) / seconds_per_year
        growth = Decimal(str(exp(float(risk_free_rate * maturity))))
        forward = strike + growth * (call_mid - put_mid)
        if forward > 0:
            candidates.append(forward)
    if not candidates:
        return None, 0
    ordered = sorted(candidates)
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    return median, len(ordered)
