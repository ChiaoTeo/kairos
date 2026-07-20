from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

from kairos.data.market_snapshot_storage import MarketSnapshotStorageDriver
from kairos.backtest.synthetic_scenarios import build_synthetic_backtest_dataset
from kairos.domain.market_data import Quote
from kairos.research_platform.data_store import MarketSnapshotCollectionPublisher, merge_datasets


class ResearchDataStoreTests(unittest.TestCase):
    def test_sessions_append_idempotently_with_provenance(self) -> None:
        first = build_synthetic_backtest_dataset(start_date=date(2025, 1, 6))
        second = build_synthetic_backtest_dataset(start_date=date(2025, 1, 8))
        with tempfile.TemporaryDirectory() as directory:
            store = MarketSnapshotCollectionPublisher(MarketSnapshotStorageDriver(directory))
            store.save_session(first, append=False, collected_at=datetime(2025, 1, 6, tzinfo=timezone.utc))
            merged = store.save_session(second, append=True, collected_at=datetime(2025, 1, 8, tzinfo=timezone.utc))
            replay = store.save_session(second, append=True, collected_at=datetime(2025, 1, 8, tzinfo=timezone.utc))
            collection = store.load_collection(first.manifest.dataset_id)
        self.assertEqual(len(merged.slices), len(first.slices) + len(second.slices))
        self.assertEqual(replay.manifest.content_hash, merged.manifest.content_hash)
        self.assertEqual(len(collection.sessions), 2)
        self.assertEqual(collection.real_session_count, 0)

    def test_conflicting_slice_and_provenance_change_are_rejected(self) -> None:
        dataset = build_synthetic_backtest_dataset()
        market = dataset.slices[0]
        item = market.instruments[0]
        changed_quote = replace(item.quote, bid=item.quote.bid + Decimal("1"))
        changed_item = replace(item, quote=changed_quote)
        changed_market = replace(market, instruments=(changed_item, *market.instruments[1:]))
        conflicting = replace(dataset, slices=(changed_market, *dataset.slices[1:]))
        with self.assertRaisesRegex(ValueError, "conflicting market slice"):
            merge_datasets(dataset, conflicting)
        real_manifest = replace(dataset.manifest, synthetic=False, source="ibkr.series")
        with self.assertRaisesRegex(ValueError, "synthetic provenance"):
            merge_datasets(dataset, replace(dataset, manifest=real_manifest))


if __name__ == "__main__":
    unittest.main()
