from __future__ import annotations

import csv
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from trading.domain.event import GreeksUpdated, QuoteUpdated, UnderlyingPriceUpdated, envelope
from trading.domain.identity import AssetId, InstrumentId, VenueId
from trading.domain.instrument import InstrumentDefinition, OptionChain, VenueListing
from trading.domain.market_data import Greeks, Quote
from trading.domain.product import IndexSpec, ProductType
from trading.__main__ import main
from trading.research.service import ResearchService
from trading.research.spec import ResearchSpec
from trading.storage.repository import FileResearchRepository


class FakeProvider:
    def __init__(self) -> None:
        self.connected = False
        self.underlying_id = InstrumentDefinition(
            InstrumentId("index:spx"), ProductType.INDEX, "SPX", None, AssetId("USD"), IndexSpec(AssetId("USD")),
            (VenueListing(VenueId("ibkr"), "100", "SPX", Decimal("0.01"), Decimal("1"), Decimal("1")),),
            datetime(1970, 1, 1, tzinfo=timezone.utc),
        )

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def underlying(self, spec: ResearchSpec) -> InstrumentDefinition:
        return self.underlying_id

    def discover_option_chain(self, underlying: InstrumentDefinition, spec: ResearchSpec) -> OptionChain:
        return OptionChain(underlying.instrument_id, VenueId("ibkr"), "SMART", "SPXW", Decimal("100"), (date(2099, 1, 2),), (Decimal("5950"), Decimal("6000"), Decimal("6050")))

    def qualify(self, instruments):
        return tuple(replace(item, listings=(replace(item.listings[0], external_id=str(index + 200)),)) for index, item in enumerate(instruments))

    def snapshot(self, instruments, correlation_id: UUID):
        now = datetime.now(timezone.utc)
        if instruments == (self.underlying_id,):
            return [envelope(UnderlyingPriceUpdated(self.underlying_id.instrument_id, Decimal("6000")), source="fake", event_time=now, correlation_id=correlation_id)]
        events = []
        for definition in instruments:
            events.extend((
                envelope(QuoteUpdated(Quote(definition.instrument_id, Decimal("9"), Decimal("11"), Decimal("10"), Decimal("10"), now)), source="fake", event_time=now, correlation_id=correlation_id),
                envelope(GreeksUpdated(Greeks(definition.instrument_id, Decimal("0.2"), Decimal("0.5"), Decimal("0.01"), Decimal("-2"), Decimal("1"), now)), source="fake", event_time=now, correlation_id=correlation_id),
            ))
        return events


class ServiceTests(unittest.TestCase):
    def test_capture_save_and_offline_reproduce(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = FileResearchRepository(Path(directory))
            service = ResearchService(repository)
            provider = FakeProvider()
            snapshot, online = service.capture(provider, ResearchSpec(strikes_each_side=1, max_quote_age_seconds=60))
            self.assertFalse(provider.connected)
            run_dir = repository.run_dir(snapshot.run_id)
            self.assertEqual(
                {path.name for path in run_dir.iterdir()},
                {"manifest.json", "option_chain.json", "market_events.jsonl", "snapshot.json", "report.csv"},
            )
            manifest = repository.load_manifest(snapshot.run_id)
            self.assertEqual(manifest["status"], "completed")
            self.assertTrue(manifest["offline_analyzable"])
            events = repository.load_events(snapshot.run_id)
            self.assertEqual(len(events), manifest["collected_event_count"])
            offline = service.analyze_offline(snapshot.run_id)
            self.assertEqual(offline.rows, online.rows)
            self.assertEqual(offline.completeness_rate, Decimal("1"))
            self.assertEqual(len(offline.iv_smile), 6)
            self.assertEqual(len(offline.put_call_pairs), 3)
            self.assertTrue(all(row.paired_mid is not None for row in offline.rows))
            with (run_dir / "report.csv").open() as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 6)
            with __import__("io").StringIO() as output, redirect_stdout(output):
                self.assertEqual(main(["--data-root", directory, "research", "analyze", "--run-id", str(snapshot.run_id)]), 0)
                self.assertIn("Completeness: 100.0%", output.getvalue())
            with __import__("io").StringIO() as output, redirect_stdout(output):
                self.assertEqual(main(["--data-root", directory, "research", "show", "--run-id", str(snapshot.run_id)]), 0)
                self.assertIn("Status: completed", output.getvalue())

    def test_failure_is_persisted_and_disconnects(self) -> None:
        class BrokenProvider(FakeProvider):
            def underlying(self, spec: ResearchSpec) -> InstrumentDefinition:
                raise RuntimeError("broken feed")

        with tempfile.TemporaryDirectory() as directory:
            repository = FileResearchRepository(directory)
            provider = BrokenProvider()
            with self.assertRaisesRegex(RuntimeError, "broken feed"):
                ResearchService(repository).capture(provider, ResearchSpec())
            manifests = list(Path(directory).glob("*/*/manifest.json"))
            self.assertEqual(len(manifests), 1)
            manifest = __import__("json").loads(manifests[0].read_text())
            self.assertEqual(manifest["status"], "failed")
            self.assertEqual(manifest["error_stage"], "discovering")
            self.assertFalse(provider.connected)


if __name__ == "__main__":
    unittest.main()
