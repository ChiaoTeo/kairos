from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from math import exp
from pathlib import Path

from trading import __version__
from trading.backtest.feed import ContractMetadata, DatasetRepository, HistoricalDataset, MarketSlice, SettlementType, build_manifest
from trading.catalog.repository import CatalogRepository
from trading.domain.market_data import Quote
from trading.domain.product import ListedOptionSpec, OptionRight, ProductType, SettlementSession
from trading.market_data import MarketEventType, ParquetMarketEventRepository
from trading.research.data_store import ResearchDatasetStore
from trading.research.snapshot import DataQualityIssue, InstrumentSnapshot


class MassiveCuratedSliceBuilder:
    def __init__(self, lake_root: str | Path = "data", *, catalog_path: str | Path = "data/catalog/instruments.json",
                 dataset_root: str | Path = "data/datasets") -> None:
        self.lake_root = Path(lake_root)
        self.catalog = CatalogRepository(catalog_path).load()
        self.events = ParquetMarketEventRepository(self.lake_root / "canonical" / "market")
        self.repository = DatasetRepository(dataset_root)

    def build(self, source_dataset_id: str, output_dataset_id: str, start: datetime, end: datetime, *,
              sampling_seconds: int = 60, max_quote_age_seconds: int = 300, split: str = "development",
              risk_free_rate: Decimal = Decimal("0")) -> HistoricalDataset:
        if start.tzinfo is None or end.tzinfo is None or not start < end:
            raise ValueError("curated slice builder requires timezone-aware [start,end)")
        if sampling_seconds <= 0 or max_quote_age_seconds <= 0:
            raise ValueError("sampling and quote age must be positive")
        quote_ids = set(self.events.distinct_instruments(source_dataset_id, start, end, event_types=(MarketEventType.QUOTE,)))
        bar_ids = set(self.events.distinct_instruments(source_dataset_id, start, end, event_types=(MarketEventType.BAR,)))
        definitions = self.catalog.definitions(start)
        options = tuple(item for item in definitions if item.product_type is ProductType.LISTED_OPTION and item.instrument_id in quote_ids)
        if not options:
            raise ValueError("catalog contains no active listed options")
        option_ids = {item.instrument_id for item in options}
        underlying_ids = {item.product_spec.underlying for item in options if isinstance(item.product_spec, ListedOptionSpec)}
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
        manifest = build_manifest(output_dataset_id, tuple(slices), contracts, used_definitions,
                                  sampling_seconds=sampling_seconds,
                                  source=f"massive.canonical:{source_dataset_id};reference=official_or_put_call_parity;r={risk_free_rate}",
                                  market_data_type="historical_quotes", code_version=__version__, split=split, synthetic=False)
        dataset = HistoricalDataset(manifest, tuple(slices), contracts, used_definitions)
        ResearchDatasetStore(self.repository).save_session(dataset, append=False)
        return dataset


def _contract(definition) -> ContractMetadata:
    spec = definition.product_spec
    if not isinstance(spec, ListedOptionSpec):
        raise TypeError("contract metadata requires ListedOptionSpec")
    return ContractMetadata(definition.instrument_id, spec.last_trade_at, spec.expiry,
                            SettlementType.AM if spec.settlement_session is SettlementSession.AM else SettlementType.PM,
                            None, False, "massive.options-contracts")


def _decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


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
        spec = definition.product_spec
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
