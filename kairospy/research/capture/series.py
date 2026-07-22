from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from time import sleep
from typing import Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from kairospy import __version__
from kairospy.market.snapshots import InstrumentLifecycleSnapshot, MarketReplayDataset, MarketSnapshot, SettlementType, build_manifest
from kairospy.data.market_snapshot_storage import MarketSnapshotStorageDriver
from kairospy.integrations.connectors.ibkr.option_chain_provider import SpxwOptionChainProvider
from kairospy.market.events import UnderlyingPriceUpdated
from kairospy.market.state import MarketState, apply_market_event
from kairospy.reference.contracts import ListedOptionSpec
from kairospy.reference.access import contract_spec
from kairospy.research.capture.option_universe_selector import select_instruments
from kairospy.market.slices import DataQualityIssue, InstrumentSnapshot
from kairospy.research.capture.spec import OptionChainCaptureSpec
from kairospy.research.capture.data_store import MarketSnapshotCollectionPublisher
from kairospy.research.capture.retention import DeltaLegWatchlist
from kairospy.analytics.pricing import OptionValuationService
from kairospy.market import OptionMarketObservation, validate_option_observation


@dataclass(frozen=True, slots=True)
class SeriesCaptureSpec:
    dataset_id: str
    sample_count: int = 60
    interval_seconds: int = 60
    split: str = "development"
    checkpoint_samples: int = 10

    def __post_init__(self) -> None:
        if self.sample_count < 2:
            raise ValueError("series capture requires at least two samples")
        if self.interval_seconds < 1:
            raise ValueError("capture interval must be positive")
        if self.split not in {"development", "validation", "test"}:
            raise ValueError("invalid sample split")
        if self.checkpoint_samples < 1:
            raise ValueError("checkpoint sample count must be positive")


@dataclass(frozen=True, slots=True)
class SeriesCaptureProgress:
    completed_samples: int
    total_samples: int
    requested_contracts: int
    qualified_contracts: int
    quoted_contracts: int
    checkpoint_saved: bool
    timestamp: datetime


class SeriesCaptureService:
    def __init__(
        self,
        repository: MarketSnapshotStorageDriver,
        *,
        wait: Callable[[float], None] = sleep,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        on_progress: Callable[[SeriesCaptureProgress], None] | None = None,
    ) -> None:
        self.repository = repository
        self.wait = wait
        self.now = now
        self.on_progress = on_progress

    def capture(self, provider: SpxwOptionChainProvider, spec: OptionChainCaptureSpec, series: SeriesCaptureSpec, *, append: bool = False) -> MarketReplayDataset:
        correlation_id = uuid4()
        provider.connect()
        try:
            underlying = provider.underlying(spec)
            initial = provider.snapshot((underlying,), correlation_id)
            price = next((event.payload.price for event in initial if isinstance(event.payload, UnderlyingPriceUpdated)), None)
            if price is None:
                raise RuntimeError("underlying price is unavailable")
            slices = []
            pending_slices = []
            definitions_by_id = {}
            qualification_attempts = set()
            store = MarketSnapshotCollectionPublisher(self.repository)
            persisted = None
            watchlist = None
            known_definitions = {}
            target = self.repository.root / series.dataset_id / "dataset.json"
            if spec.retain_delta_legs:
                watchlist = DeltaLegWatchlist(
                    self.repository.root,
                    series.dataset_id,
                    evaluation_time=time.fromisoformat(spec.retention_evaluation_time),
                    target_deltas=tuple(Decimal(str(value)) for value in spec.retention_target_deltas),
                    retain_until_dte=spec.retention_until_dte,
                )
                if append and target.exists():
                    existing = self.repository.load(series.dataset_id)
                    known_definitions.update((item.instrument_id, item) for item in existing.definitions)
            restored_watchlist = False
            last_timestamp = None
            for index in range(series.sample_count):
                retention_time = last_timestamp or datetime.now(timezone.utc)
                if watchlist is not None and not restored_watchlist:
                    active = watchlist.active_definitions(retention_time, known_definitions)
                    qualification_attempts.update(item.instrument_id for item in active)
                    definitions_by_id.update((item.instrument_id, item) for item in provider.qualify(active))
                    restored_watchlist = True
                chain = provider.discover_option_chain(underlying, spec)
                requested = select_instruments(provider.catalog, chain, price, spec)
                missing = tuple(item for item in requested if item.instrument_id not in qualification_attempts)
                qualification_attempts.update(item.instrument_id for item in missing)
                definitions_by_id.update((item.instrument_id, item) for item in provider.qualify(missing))
                current = tuple(definitions_by_id[item.instrument_id] for item in requested if item.instrument_id in definitions_by_id)
                known_definitions.update(definitions_by_id)
                retained = watchlist.active_definitions(retention_time, known_definitions) if watchlist else ()
                selected_by_id = {item.instrument_id: item for item in (*current, *retained)}
                selected = tuple(sorted(selected_by_id.values(), key=lambda item: item.instrument_id.value))
                if not selected:
                    raise RuntimeError("no option contracts qualified for series capture")
                definitions_by_id.update((item.instrument_id, item) for item in selected)
                sample_events = provider.snapshot((underlying, *selected), correlation_id)
                state = MarketState()
                for event in sample_events:
                    apply_market_event(state, event)
                price_entry = state.underlying_prices.get(underlying.instrument_id)
                timestamp = self.now()
                if timestamp.tzinfo is None:
                    raise ValueError("series clock must be timezone-aware")
                last_timestamp = timestamp
                issues = []
                if price_entry is None:
                    issues.append(DataQualityIssue("missing_underlying", "underlying price missing", "error", underlying.instrument_id))
                    slice_price = price
                else:
                    slice_price = price_entry[0]
                    price = slice_price
                snapshots = []
                event_times = []
                for definition in selected:
                    instrument_id = definition.instrument_id
                    item = state.instruments.get(instrument_id)
                    if item is None:
                        snapshots.append(InstrumentSnapshot(instrument_id, None, None, None, None, None, None))
                        issues.append(DataQualityIssue("missing_market_data", "option market data missing", "error", instrument_id))
                    else:
                        snapshots.append(InstrumentSnapshot(instrument_id, item.quote, item.quote_time, item.trade, item.trade_time, item.greeks, item.greeks_time))
                        event_times.extend(value for value in (item.quote_time, item.trade_time, item.greeks_time) if value is not None)
                        if item.quote is not None:
                            quality = validate_option_observation(
                                OptionMarketObservation(
                                    instrument_id, item.quote.event_time, item.quote.bid, item.quote.ask,
                                    item.quote.bid_size, item.quote.ask_size, "ibkr.series",
                                ),
                                timestamp,
                                max_age_seconds=Decimal(str(spec.max_quote_age_seconds)),
                            )
                            issues.extend(DataQualityIssue(issue.code, issue.message, issue.severity, instrument_id) for issue in quality)
                span = Decimal(str((max(event_times) - min(event_times)).total_seconds())) if event_times else Decimal("0")
                universe = tuple(item.instrument_id for item in selected)
                market_snapshot = MarketSnapshot(timestamp, tuple(snapshots), ((underlying.instrument_id, slice_price),), tuple(issues), span, index, universe)
                if watchlist is not None:
                    selected_legs = watchlist.observe(market_snapshot, current)
                    if not selected_legs:
                        valued_market, _ = OptionValuationService(provider.catalog).value(market_snapshot)
                        watchlist.observe(valued_market, current)
                slices.append(market_snapshot)
                pending_slices.append(market_snapshot)
                checkpoint_saved = False
                if len(pending_slices) >= series.checkpoint_samples or index + 1 == series.sample_count:
                    selected_so_far = tuple(sorted(definitions_by_id.values(), key=lambda item: item.instrument_id.value))
                    contracts_so_far = tuple(
                        InstrumentLifecycleSnapshot(
                            definition.instrument_id,
                            contract_spec(definition).last_trade_at,
                            contract_spec(definition).expiry + timedelta(minutes=1),
                            SettlementType.PM,
                            None,
                            False,
                            "inferred.spxw-default",
                        )
                        for definition in selected_so_far
                        if isinstance(contract_spec(definition), ListedOptionSpec)
                    )
                    chunk_slices = tuple(pending_slices)
                    chunk_manifest = build_manifest(
                        series.dataset_id, chunk_slices, contracts_so_far, (underlying, *selected_so_far),
                        sampling_seconds=series.interval_seconds, source="ibkr.series",
                        market_data_type=spec.market_data_type.value, code_version=__version__,
                        split=series.split, synthetic=False,
                        products=tuple(provider.catalog.products.values()),
                        references=provider.catalog.all_references(),
                        settlements=provider.catalog.settlements.values(),
                    )
                    chunk = MarketReplayDataset(
                        chunk_manifest, chunk_slices, contracts_so_far, (underlying, *selected_so_far),
                        provider.catalog.products.values(), provider.catalog.all_references(),
                        provider.catalog.settlements.values(),
                    )
                    persisted = store.save_session(chunk, append=append or persisted is not None, collected_at=timestamp)
                    pending_slices.clear()
                    checkpoint_saved = True
                if self.on_progress is not None:
                    self.on_progress(SeriesCaptureProgress(
                        index + 1,
                        series.sample_count,
                        len(requested),
                        len(selected),
                        sum(snapshot.quote is not None for snapshot in snapshots),
                        checkpoint_saved,
                        timestamp,
                    ))
                if index + 1 < series.sample_count:
                    self.wait(series.interval_seconds)
            if persisted is None:
                raise RuntimeError("series capture produced no persisted checkpoints")
            return persisted
        finally:
            provider.disconnect()
