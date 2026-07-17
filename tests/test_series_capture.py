from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from trading.data.market_slice_storage import MarketSliceStorageDriver
from trading.domain.event import GreeksUpdated, QuoteUpdated, UnderlyingPriceUpdated, envelope
from trading.domain.identity import AssetId, InstrumentId, VenueId
from trading.domain.market_data import OptionChain
from trading.domain.market_data import Greeks, Quote
from trading.domain.product import IndexSpec, OptionRight, ProductType
from trading.research.series import SeriesCaptureService, SeriesCaptureSpec
from trading.research.data_store import MarketSliceCollectionPublisher
from trading.research.spec import ResearchSpec
from trading.reference import ReferenceCatalog
from tests.reference_support import publish_test_instrument


class SeriesProvider:
    def __init__(self) -> None:
        self.connected = False
        self.catalog = ReferenceCatalog()
        self.underlying_id = publish_test_instrument(
            self.catalog, InstrumentId("index:spx"), ProductType.INDEX, "SPX", IndexSpec(AssetId("USD")),
            AssetId("USD"), VenueId("ibkr"), "SPX", datetime(1970, 1, 1, tzinfo=timezone.utc),
        )

    def connect(self): self.connected = True
    def disconnect(self): self.connected = False
    def underlying(self, spec): return self.underlying_id

    def discover_option_chain(self, underlying, spec):
        return OptionChain(underlying.instrument_id, VenueId("ibkr"), "SMART", "SPXW", Decimal("100"), (date(2099, 1, 2),), (Decimal("5950"), Decimal("6000"), Decimal("6050")))

    def qualify(self, instruments):
        return tuple(instruments)

    def snapshot(self, instruments, correlation_id):
        now = datetime(2099, 1, 1, 12, tzinfo=timezone.utc)
        events = []
        for definition in instruments:
            if definition.instrument_type is ProductType.INDEX:
                events.append(envelope(UnderlyingPriceUpdated(definition.instrument_id, Decimal("6000")), source="fake.series", event_time=now, correlation_id=correlation_id))
            else:
                events.extend((
                    envelope(QuoteUpdated(Quote(definition.instrument_id, Decimal("9"), Decimal("11"), Decimal("10"), Decimal("10"), now)), source="fake.series", event_time=now, correlation_id=correlation_id),
                    envelope(GreeksUpdated(Greeks(definition.instrument_id, Decimal("0.2"), Decimal("-0.2"), Decimal("0.01"), Decimal("-1"), Decimal("1"), now)), source="fake.series", event_time=now, correlation_id=correlation_id),
                ))
        return events


class SeriesCaptureTests(unittest.TestCase):
    def test_failed_qualifications_are_not_retried_within_a_session(self) -> None:
        class PartiallyQualifiedProvider(SeriesProvider):
            def __init__(self):
                super().__init__()
                self.qualification_batches = []

            def qualify(self, instruments):
                self.qualification_batches.append(tuple(item.instrument_id for item in instruments))
                return super().qualify(instruments[:1])

        provider = PartiallyQualifiedProvider()
        progress = []
        times = iter(datetime(2099, 1, 1, 12, minute, tzinfo=timezone.utc) for minute in range(3))
        with tempfile.TemporaryDirectory() as directory:
            SeriesCaptureService(
                MarketSliceStorageDriver(directory), wait=lambda _: None, now=lambda: next(times),
                on_progress=progress.append,
            ).capture(
                provider, ResearchSpec(strikes_each_side=1, rights=(OptionRight.PUT,)),
                SeriesCaptureSpec("no-retry", 2, 60),
            )

        self.assertEqual([len(batch) for batch in provider.qualification_batches], [3, 0])
        self.assertEqual([item.qualified_contracts for item in progress], [1, 1])
        self.assertTrue(progress[-1].checkpoint_saved)

    def test_fixed_frequency_capture_persists_replayable_dataset(self) -> None:
        provider = SeriesProvider()
        times = iter(datetime(2099, 1, 1, 12, minute, tzinfo=timezone.utc) for minute in range(3))
        waits = []
        with tempfile.TemporaryDirectory() as directory:
            repository = MarketSliceStorageDriver(directory)
            service = SeriesCaptureService(repository, wait=waits.append, now=lambda: next(times))
            dataset = service.capture(
                provider,
                ResearchSpec(strikes_each_side=1),
                SeriesCaptureSpec("series-fixture", sample_count=3, interval_seconds=60),
            )
            self.assertFalse(provider.connected)
            self.assertEqual(dataset.manifest.slice_count, 3)
            self.assertEqual(dataset.manifest.quote_coverage, Decimal("1"))
            self.assertEqual(waits, [60, 60])
            loaded = repository.load("series-fixture")
            self.assertEqual(loaded, dataset)
            self.assertFalse(loaded.manifest.synthetic)
            self.assertTrue(all(a.timestamp < b.timestamp for a, b in zip(loaded.slices, loaded.slices[1:])))
            self.assertTrue(all(item.available_instruments for item in loaded.slices))

    def test_option_universe_is_rediscovered_at_each_point_in_time(self) -> None:
        class DynamicProvider(SeriesProvider):
            def __init__(self):
                super().__init__()
                self.discovery_count = 0

            def discover_option_chain(self, underlying, spec):
                self.discovery_count += 1
                strikes = (Decimal("5950"), Decimal("6000"), Decimal("6050")) if self.discovery_count == 1 else (Decimal("6000"), Decimal("6050"), Decimal("6100"))
                return OptionChain(underlying.instrument_id, VenueId("ibkr"), "SMART", "SPXW", Decimal("100"), (date(2099, 1, 2),), strikes)

        provider = DynamicProvider()
        times = iter((datetime(2099, 1, 1, 12, tzinfo=timezone.utc), datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)))
        with tempfile.TemporaryDirectory() as directory:
            dataset = SeriesCaptureService(MarketSliceStorageDriver(directory), wait=lambda _: None, now=lambda: next(times)).capture(
                provider, ResearchSpec(strikes_each_side=1), SeriesCaptureSpec("dynamic-series", 2, 60),
            )
        self.assertEqual(provider.discovery_count, 2)
        self.assertNotEqual(dataset.slices[0].available_instruments, dataset.slices[1].available_instruments)
        known = {item.instrument_id for item in dataset.definitions}
        self.assertTrue(set(dataset.slices[0].available_instruments) | set(dataset.slices[1].available_instruments) <= known)

    def test_independent_gateway_sessions_append_to_one_research_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = MarketSliceStorageDriver(directory)
            first_times = iter(datetime(2099, 1, 1, 12, minute, tzinfo=timezone.utc) for minute in range(2))
            first = SeriesCaptureService(repository, wait=lambda _: None, now=lambda: next(first_times)).capture(
                SeriesProvider(), ResearchSpec(strikes_each_side=1), SeriesCaptureSpec("gateway-resume", 2, 60), append=True,
            )
            second_times = iter(datetime(2099, 1, 1, 12, minute, tzinfo=timezone.utc) for minute in range(2, 4))
            merged = SeriesCaptureService(repository, wait=lambda _: None, now=lambda: next(second_times)).capture(
                SeriesProvider(), ResearchSpec(strikes_each_side=1), SeriesCaptureSpec("gateway-resume", 2, 60), append=True,
            )
            collection = MarketSliceCollectionPublisher(repository).load_collection("gateway-resume")
        self.assertEqual(len(first.slices), 2)
        self.assertEqual(len(merged.slices), 4)
        self.assertEqual(len(collection.sessions), 2)
        self.assertEqual(collection.real_session_count, 2)

    def test_checkpoint_survives_provider_failure(self) -> None:
        class FailingProvider(SeriesProvider):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def snapshot(self, instruments, correlation_id):
                self.calls += 1
                if self.calls == 4:  # initial underlying + two completed samples + failure
                    raise RuntimeError("simulated gateway disconnect")
                return super().snapshot(instruments, correlation_id)

        provider = FailingProvider()
        times = iter(datetime(2099, 1, 1, 12, minute, tzinfo=timezone.utc) for minute in range(3))
        with tempfile.TemporaryDirectory() as directory:
            repository = MarketSliceStorageDriver(directory)
            service = SeriesCaptureService(repository, wait=lambda _: None, now=lambda: next(times))
            with self.assertRaisesRegex(RuntimeError, "gateway disconnect"):
                service.capture(
                    provider, ResearchSpec(strikes_each_side=1),
                    SeriesCaptureSpec("checkpoint-recovery", 4, 60, checkpoint_samples=1), append=True,
                )
            recovered = repository.load("checkpoint-recovery")
            collection = MarketSliceCollectionPublisher(repository).load_collection("checkpoint-recovery")
        self.assertFalse(provider.connected)
        self.assertEqual(len(recovered.slices), 2)
        self.assertEqual(len(collection.sessions), 2)


if __name__ == "__main__":
    unittest.main()
