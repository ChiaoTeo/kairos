from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from time import sleep
from typing import Callable, Protocol

from trading import __version__
from trading.backtest.feed import ContractMetadata, HistoricalDataset, MarketSlice, SettlementType, build_manifest
from trading.data.market_slice_storage import MarketSliceStorageDriver
from trading.domain.product import FutureSpec, ListedOptionSpec, is_option_spec
from trading.reference.models import InstrumentDefinition
from trading.reference.catalog import ReferenceCatalog
from trading.reference.access import contract_spec
from trading.research.snapshot import DataQualityIssue, InstrumentSnapshot
from trading.research.series import SeriesCaptureSpec


class NormalizedQuoteProvider(Protocol):
    def snapshot(self, instruments: tuple[InstrumentDefinition, ...]): ...


class NormalizedSeriesCaptureService:
    def __init__(self, repository: MarketSliceStorageDriver, *, wait: Callable[[float], None] = sleep, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.repository, self.wait, self.now = repository, wait, now

    def capture(self, provider: NormalizedQuoteProvider, catalog: ReferenceCatalog,
                definitions: tuple[InstrumentDefinition, ...], series: SeriesCaptureSpec, *,
                source: str, market_data_type: str) -> HistoricalDataset:
        if not definitions:
            raise ValueError("normalized series capture requires instruments")
        slices = []
        for sequence in range(series.sample_count):
            timestamp = self.now()
            if timestamp.tzinfo is None:
                raise ValueError("series clock must be timezone-aware")
            quotes = {item.instrument_id: item for item in provider.snapshot(definitions)}
            snapshots, issues, event_times = [], [], []
            for definition in definitions:
                quote = quotes.get(definition.instrument_id)
                if quote is None:
                    issues.append(DataQualityIssue("missing_quote", "normalized quote is missing", "error", definition.instrument_id))
                else:
                    event_times.append(quote.event_time)
                snapshots.append(InstrumentSnapshot(
                    definition.instrument_id, quote, quote.event_time if quote else None,
                    None, None, None, None,
                ))
            span = Decimal(str((max(event_times) - min(event_times)).total_seconds())) if event_times else Decimal("0")
            slices.append(MarketSlice(timestamp, tuple(snapshots), (), tuple(issues), span, sequence))
            if sequence + 1 < series.sample_count:
                self.wait(series.interval_seconds)
        contracts = tuple(_contract_metadata(item) for item in definitions if _is_expiring(item))
        slice_tuple = tuple(slices)
        manifest = build_manifest(
            series.dataset_id, slice_tuple, contracts, definitions,
            sampling_seconds=series.interval_seconds, source=source, market_data_type=market_data_type,
            code_version=__version__, split=series.split, synthetic=False,
            products=tuple(catalog.products.get(item.product_id, item.effective_from) for item in definitions),
            references=tuple(item for item in catalog.all_references() if item.source_instrument_id in {value.instrument_id for value in definitions}),
            settlements=tuple(catalog.settlements.get(item.settlement_terms_id, item.effective_from)
                              for item in definitions if item.settlement_terms_id is not None),
        )
        product_ids = {item.product_id for item in definitions}
        instrument_ids = {item.instrument_id for item in definitions}
        dataset = HistoricalDataset(
            manifest, slice_tuple, contracts, definitions,
            tuple(item for item in catalog.products.values() if item.product_id in product_ids),
            tuple(item for item in catalog.all_references() if item.source_instrument_id in instrument_ids),
            tuple(catalog.settlements.get(item.settlement_terms_id, item.effective_from)
                  for item in definitions if item.settlement_terms_id is not None),
        )
        self.repository.save(dataset)
        return dataset


def _is_expiring(definition: InstrumentDefinition) -> bool:
    spec = contract_spec(definition)
    return is_option_spec(spec) or isinstance(spec, FutureSpec)


def _contract_metadata(definition: InstrumentDefinition) -> ContractMetadata:
    spec = contract_spec(definition)
    last_trade_at = spec.last_trade_at if isinstance(spec, ListedOptionSpec) else spec.expiry
    return ContractMetadata(
        definition.instrument_id, last_trade_at, spec.expiry,
        SettlementType.PM, None, False, "catalog.normalized-series",
    )
