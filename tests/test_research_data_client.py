from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import redirect_stdout
import json
import io
import unittest

from kairos.data import (
    AcquirePolicy, AcquisitionLimits, DataCatalog, DataUnavailableError, DatasetKey, DatasetLayer,
    DataProductDefinition, DatasetRelease, DatasetStatus, DatasetStorageKind, FieldRef, OutputFormat, ProviderRegistry,
    QualityLevel, ResearchDataClient, RunMode, SourceBinding, TimeRange,
    ConsolidatedTradeBuilder, ConsolidatedTradeInput, ConsolidatedTradePolicy,
)
from kairos.domain.identity import InstrumentId
from kairos.connectors.binance.datasets import BinanceSpotDatasetConnector
from kairos.market_data import MarketEventEnvelope, MarketEventType, ParquetMarketEventRepository
from kairos.storage.data_lake import write_daily_dataset, write_event_dataset
from kairos.data.products import BTC_SPOT_DAILY
from kairos.data.publishing import content_release_id, merge_release_rows, publish_release, release_path
from kairos.data.market_snapshot_storage import MarketSnapshotStorageDriver
from kairos.backtest.synthetic_scenarios import build_synthetic_backtest_dataset
from kairos.__main__ import main


NOW = datetime(2026, 7, 15, 14, 30, tzinfo=timezone.utc)


def register_managed(catalog, dataset, *, release_id=None, version="1", aliases=()):
    catalog.register_product(dataset.product)
    release = DatasetRelease(
        release_id or str(dataset.key), dataset.key, version, dataset.schema_id, "1", "test", "1",
        dataset.relative_path, "parquet", "test-content-hash", aliases=aliases,
    )
    catalog.register_release(release)
    return release


class ResearchDataClientTests(unittest.TestCase):
    def test_incremental_merge_normalizes_equivalent_utc_primary_keys(self):
        rows = merge_release_rows("/tmp/unused", None, [
            {"period_start": "2026-01-01T00:00:00Z", "instrument_id": "BTC", "close": 1},
            {"period_start": "2026-01-01T00:00:00+00:00", "instrument_id": "BTC", "close": 2},
        ], primary_key=("period_start", "instrument_id"), order_by=("period_start",))
        self.assertEqual(rows, [{"period_start": "2026-01-01T00:00:00+00:00", "instrument_id": "BTC", "close": 2}])

    def test_generic_data_plan_cli_is_read_only_and_reports_missing_range(self):
        with TemporaryDirectory() as temporary, io.StringIO() as output, redirect_stdout(output):
            result = main([
                "--lake-root", temporary, "data", "plan", "--dataset", str(BTC_SPOT_DAILY.key),
                "--start", "2026-01-01T00:00:00+00:00", "--end", "2026-01-02T00:00:00+00:00",
                "--provider", "binance", "--venue", "binance",
            ])
            self.assertEqual(result, 0)
            payload = json.loads(output.getvalue())
            self.assertFalse(payload["missing"] == [])
            self.assertEqual(payload["selected"]["provider"], "binance")
            self.assertEqual(payload["source_policy_version"], "priority-v1")

    def test_catalog_v3_round_trips_products_releases_and_structured_sources(self):
        with TemporaryDirectory() as temporary:
            catalog = DataCatalog(temporary)
            product = DataProductDefinition(DatasetKey("market.trades.crypto.btc_usdt"), "Binance BTC trades",
                DatasetLayer.CANONICAL, dimensions={"venue": "binance", "asset_class": "crypto"},
                sources=(SourceBinding("binance", "binance", 100, QualityLevel.BACKTEST, ("rest",)),))
            catalog.register_product(product)
            catalog.register_release(DatasetRelease(
                "ds_btc_1", product.key, "2026.07.16.1", "market.trade", "1", "binance.trades", "1",
                "canonical/market/dataset=ds_btc_1", "parquet", "sha256:test", "binance", "binance",
                ("btc-trades@research",), DatasetStatus.APPROVED_FOR_BACKTEST, QualityLevel.BACKTEST,
            ))
            catalog.save()
            loaded = DataCatalog(temporary)
            self.assertEqual(loaded.product(product.key).dimensions["venue"], "binance")
            self.assertEqual(loaded.product(product.key).source_policy_version, "priority-v1")
            self.assertEqual(loaded.release("btc-trades@research").release_id, "ds_btc_1")
            self.assertEqual(loaded.search(venue="binance"), (product,))
            client = ResearchDataClient(temporary)
            self.assertEqual(client.search(venue="binance"), (product,))
            self.assertEqual(client.describe(product)["selected_release"]["release_id"], "ds_btc_1")

    def test_typed_product_resolution_is_not_shadowed_by_same_named_earlier_release(self):
        with TemporaryDirectory() as temporary:
            catalog = DataCatalog(temporary)
            product = DataProductDefinition(DatasetKey("market.shadow.test"), "Shadow test", DatasetLayer.CANONICAL)
            catalog.register_product(product)
            catalog.register_release(DatasetRelease(
                str(product.key), product.key, "1", "trade", "1", "earlier", "1", "canonical/earlier",
                "parquet", "hash-old", published_at="2025-01-01T00:00:00Z",
            ))
            catalog.register_release(DatasetRelease(
                "ds_new", product.key, "2", "trade", "1", "new", "2", "canonical/new",
                "parquet", "hash-new", published_at="2026-01-01T00:00:00Z",
            ))
            self.assertEqual(catalog.release(product).release_id, "ds_new")
            self.assertEqual(catalog.release(product.key).release_id, "ds_new")
            self.assertEqual(catalog.release(str(product.key)).release_id, str(product.key))

    def test_versioned_catalog_resolves_logical_name_and_alias(self):
        with TemporaryDirectory() as temporary:
            catalog = DataCatalog(temporary)
            product = DataProductDefinition(DatasetKey("market.quotes.options.us.tick"), "Option quotes", DatasetLayer.CANONICAL)
            catalog.register_product(product)
            for release_id, version, path in (("quotes.v1", "1", "canonical/q1"), ("quotes.v2", "2", "canonical/q2")):
                catalog.register_release(DatasetRelease(
                    release_id, product.key, version, f"q.v{version}", version, "test", version,
                    path, "parquet", f"hash-{version}", aliases=("quotes@latest",),
                ))
            catalog.save()
            loaded = DataCatalog(temporary)
            self.assertEqual(loaded.resolve("market.quotes.options.us.tick").release_id, "quotes.v2")
            self.assertEqual(loaded.resolve("quotes@latest").release_id, "quotes.v2")
            self.assertEqual(loaded.resolve("market.quotes.options.us.tick", version="1").release_id, "quotes.v1")
            comparison = ResearchDataClient(temporary).compare("quotes.v1", "quotes.v2")
            self.assertFalse(comparison["identity"]["release_version"]["equal"])
            self.assertFalse(comparison["identity"]["schema_id"]["equal"])
            self.assertEqual((comparison["first"], comparison["second"]), ("quotes.v1", "quotes.v2"))
            self.assertEqual(comparison["schema_compatibility"]["status"], "unknown")

    def test_release_compare_reports_incompatible_schema_changes(self):
        with TemporaryDirectory() as temporary:
            catalog = DataCatalog(temporary)
            product = DataProductDefinition(DatasetKey("market.schema.test"), "Schema test", DatasetLayer.CANONICAL)
            catalog.register_product(product)
            for release_id, columns in (("schema-v1", {"price": {"type": "number"}}),
                                        ("schema-v2", {"price": {"type": "string"}})):
                directory = Path(temporary) / "canonical" / release_id; directory.mkdir(parents=True)
                (directory / "schema.json").write_text(json.dumps({"primary_key": ["id"], "columns": columns}))
                catalog.register_release(DatasetRelease(
                    release_id, product.key, release_id[-1], "trade", release_id[-1], "test", "1",
                    f"canonical/{release_id}", "parquet", f"hash-{release_id}",
                ))
            catalog.save()
            compatibility = ResearchDataClient(temporary).compare("schema-v1", "schema-v2")["schema_compatibility"]
            self.assertEqual(compatibility["status"], "incompatible")
            self.assertIn("column type changed: price", compatibility["reasons"][0])

    def test_loads_managed_dataset_without_exposing_paths(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("pyarrow optional dependency is not installed")
        with TemporaryDirectory() as temporary:
            catalog = DataCatalog(temporary); register_managed(catalog, BTC_SPOT_DAILY); catalog.save()
            root = catalog.path(str(BTC_SPOT_DAILY.key))
            write_daily_dataset(root, [
                {"period_start": "2026-01-01T00:00:00Z", "close": "100"},
                {"period_start": "2026-01-02T00:00:00Z", "close": "101"},
            ], dataset_id=str(BTC_SPOT_DAILY.key),
                schema={"schema_id": "market.ohlcv.v1"}, lineage={"source": "test"})
            client = ResearchDataClient(temporary)
            rows = client.load_rows(BTC_SPOT_DAILY.product,
                                    start="2026-01-02T00:00:00Z", fields=("period_start", "close"))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["close"], "101")

    def test_lazy_typed_query_explains_and_collects_with_pushdown(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("pyarrow optional dependency is not installed")
        with TemporaryDirectory() as temporary:
            catalog = DataCatalog(temporary); release = register_managed(catalog, BTC_SPOT_DAILY); catalog.save()
            root = catalog.path(release.release_id)
            write_daily_dataset(root, [
                {"period_start": "2026-01-01T00:00:00Z", "close": "100"},
                {"period_start": "2026-01-02T00:00:00Z", "close": "101"},
            ], dataset_id=release.release_id, schema={"schema_id": "market.ohlcv.v1"},
               lineage={"source": "test"})
            query = ResearchDataClient(temporary).get(
                DatasetKey(str(release.product_key)), start="2026-01-02T00:00:00Z",
                fields=(FieldRef("period_start"), FieldRef("close")),
            )
            self.assertTrue(query.explain()["predicate_pushdown"])
            self.assertEqual(query.collect(OutputFormat.ROWS), [
                {"period_start": "2026-01-02T00:00:00Z", "close": "101"},
            ])
            batches = tuple(ResearchDataClient(temporary).get(
                release.release_id, fields=("period_start", "close"),
            ).stream(batch_size=1))
            self.assertEqual([batch.num_rows for batch in batches], [1, 1])

    def test_parquet_queries_prune_partitions_columns_and_bound_stream_batches(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("pyarrow optional dependency is not installed")
        with TemporaryDirectory() as temporary:
            catalog = DataCatalog(temporary); release = register_managed(catalog, BTC_SPOT_DAILY); catalog.save()
            write_daily_dataset(Path(temporary) / release.relative_path, [
                {"period_start": "2026-01-01T00:00:00Z", "close": 100, "unused": "a"},
                {"period_start": "2026-01-02T00:00:00Z", "close": 101, "unused": "b"},
                {"period_start": "2026-02-01T00:00:00Z", "close": 102, "unused": "c"},
            ], dataset_id=release.release_id, schema={"schema_id": release.schema_id}, lineage={"source": "test"})
            query = ResearchDataClient(temporary).get(
                release.product_key, start="2026-01-01T00:00:00Z", end="2026-02-01T00:00:00Z",
                fields=("close",),
            )
            plan = query.explain()
            self.assertEqual(plan["partition_pruning"], {"total_files": 2, "selected_files": 1})
            batches = tuple(query.stream(batch_size=1))
            self.assertEqual([batch.num_rows for batch in batches], [1, 1])
            self.assertTrue(all(batch.schema.names == ["close"] for batch in batches))

    def test_release_scan_does_not_descend_into_nested_sibling_releases(self):
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            self.skipTest("pyarrow optional dependency is not installed")
        with TemporaryDirectory() as temporary:
            catalog = DataCatalog(temporary); release = register_managed(catalog, BTC_SPOT_DAILY); catalog.save()
            root = Path(temporary) / release.relative_path
            base = root / "event_year=2026" / "event_month=01"; base.mkdir(parents=True)
            nested = root / "release=ds_other" / "event_year=2026" / "event_month=01"; nested.mkdir(parents=True)
            pq.write_table(pa.table({"period_start": ["2026-01-01T00:00:00Z"], "close": [1]}),
                           base / "part-00000.parquet")
            pq.write_table(pa.table({"period_start": ["2026-01-01T00:00:00Z"], "close": [2]}),
                           nested / "part-00000.parquet")
            rows = ResearchDataClient(temporary).get(release.product_key, fields=("close",)).collect("rows")
            self.assertEqual(rows, [{"close": 1}])

    def test_parquet_reader_unifies_null_and_numeric_partition_schemas(self):
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            self.skipTest("pyarrow optional dependency is not installed")
        with TemporaryDirectory() as temporary:
            catalog = DataCatalog(temporary); release = register_managed(catalog, BTC_SPOT_DAILY); catalog.save()
            root = Path(temporary) / release.relative_path
            january = root / "event_year=2026" / "event_month=01"; january.mkdir(parents=True)
            february = root / "event_year=2026" / "event_month=02"; february.mkdir(parents=True)
            pq.write_table(pa.table({"period_start": ["2026-01-01T00:00:00Z"], "metric": [None]}),
                           january / "part-00000.parquet")
            pq.write_table(pa.table({"period_start": ["2026-02-01T00:00:00Z"], "metric": [1.5]}),
                           february / "part-00000.parquet")
            rows = ResearchDataClient(temporary).get(release.product_key, fields=("metric",)).collect("rows")
            self.assertEqual(rows, [{"metric": None}, {"metric": 1.5}])

    def test_parquet_reader_unifies_timestamp_and_iso_string_time_partitions(self):
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            self.skipTest("pyarrow optional dependency is not installed")
        with TemporaryDirectory() as temporary:
            catalog = DataCatalog(temporary); release = register_managed(catalog, BTC_SPOT_DAILY); catalog.save()
            root = Path(temporary) / release.relative_path
            january = root / "event_year=2026" / "event_month=01"; january.mkdir(parents=True)
            february = root / "event_year=2026" / "event_month=02"; february.mkdir(parents=True)
            pq.write_table(pa.table({"period_start": [datetime(2026, 1, 1, tzinfo=timezone.utc)], "close": [1.0]}),
                           january / "part-00000.parquet")
            pq.write_table(pa.table({"period_start": ["2026-02-01T00:00:00Z"], "close": [2.0]}),
                           february / "part-00000.parquet")
            rows = ResearchDataClient(temporary).get(
                release.product_key, start="2026-01-01T00:00:00Z", end="2026-03-01T00:00:00Z",
                fields=("period_start", "close"),
            ).collect("rows")
            self.assertEqual([row["close"] for row in rows], [1.0, 2.0])

    def test_lazy_query_freezes_release_before_alias_changes(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("pyarrow optional dependency is not installed")
        with TemporaryDirectory() as temporary:
            catalog = DataCatalog(temporary)
            product = DataProductDefinition(DatasetKey("market.prices.test"), "Test prices", DatasetLayer.CANONICAL,
                                     primary_time="period_start")
            catalog.register_product(product)
            first = DatasetRelease("prices.v1", product.key, "1", "market.ohlcv.v1", "1", "test", "1",
                                   "canonical/prices-v1", "parquet", "hash-1", aliases=("prices@research",))
            catalog.register_release(first); catalog.save()
            write_daily_dataset(
                Path(temporary) / first.relative_path,
                [{"period_start": "2026-01-01T00:00:00Z", "close": 1}], dataset_id=first.release_id,
                schema={"schema_id": first.schema_id, "primary_key": ["period_start"]}, lineage={"source": "test"},
            )
            client = ResearchDataClient(temporary)
            query = client.get("prices@research", fields=("close",))
            second = DatasetRelease("prices.v2", product.key, "2", "market.ohlcv.v1", "1", "test", "2",
                                    "canonical/prices-v2", "parquet", "hash-2", aliases=("prices@research",))
            write_daily_dataset(
                Path(temporary) / second.relative_path,
                [{"period_start": "2026-01-01T00:00:00Z", "close": 2}], dataset_id=second.release_id,
                schema={"schema_id": second.schema_id, "primary_key": ["period_start"]}, lineage={"source": "test"},
            )
            client.catalog.register_release(second)
            self.assertEqual(query.collect(OutputFormat.ROWS), [{"close": 1}])
            self.assertEqual(client.get("prices@research", fields=("close",)).collect(OutputFormat.ROWS), [{"close": 2}])

    def test_local_multi_source_selection_requires_matching_provider_and_venue(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("pyarrow optional dependency is not installed")
        with TemporaryDirectory() as temporary:
            catalog = DataCatalog(temporary)
            product = DataProductDefinition(
                DatasetKey("market.trades.crypto.btc"), "BTC trades", DatasetLayer.CANONICAL,
                sources=(SourceBinding("vendor-a", "binance", 100), SourceBinding("vendor-b", "deribit", 90)),
            )
            catalog.register_product(product)
            for release_id, provider, venue, price in (
                ("btc-a", "vendor-a", "binance", 100), ("btc-b", "vendor-b", "deribit", 200),
            ):
                path = Path(temporary) / "canonical" / release_id
                manifest = write_daily_dataset(
                    path, [{"period_start": "2026-01-01T00:00:00Z", "price": price}], dataset_id=release_id,
                    schema={"schema_id": "market.trade.v1", "primary_key": ["period_start"]},
                    lineage={"source": {"provider": provider, "venue": venue}},
                )
                catalog.register_release(DatasetRelease(
                    release_id, product.key, "1", "market.trade", "1", "test", "1",
                    f"canonical/{release_id}", "parquet", str(manifest["dataset_sha256"]), provider, venue,
                ))
            catalog.save(); client = ResearchDataClient(temporary)
            binance = client.get(product, provider="vendor-a", venue="binance", fields=("price",)).collect("rows")
            deribit = client.get(product, provider="vendor-b", venue="deribit", fields=("price",)).collect("rows")
            self.assertEqual(binance, [{"price": 100}])
            self.assertEqual(deribit, [{"price": 200}])
            with self.assertRaises(KeyError):
                client.get(product, provider="vendor-a", venue="deribit")

    def test_automatic_source_selection_follows_versioned_product_priority(self):
        with TemporaryDirectory() as temporary:
            catalog = DataCatalog(temporary)
            product = DataProductDefinition(
                DatasetKey("market.trades.priority.test"), "Priority test", DatasetLayer.CANONICAL,
                sources=(SourceBinding("preferred", "same-venue", 100),
                         SourceBinding("newer", "same-venue", 10)),
                source_policy_version="priority-v1",
            )
            catalog.register_product(product)
            catalog.register_release(DatasetRelease(
                "preferred-old", product.key, "1", "trade", "1", "test", "1", "canonical/preferred",
                "parquet", "hash-a", "preferred", "same-venue", published_at="2025-01-01T00:00:00Z",
            ))
            catalog.register_release(DatasetRelease(
                "lower-new", product.key, "2", "trade", "1", "test", "1", "canonical/lower",
                "parquet", "hash-b", "newer", "same-venue", published_at="2026-01-01T00:00:00Z",
            ))
            self.assertEqual(catalog.release(product).release_id, "preferred-old")

    def test_cross_venue_product_is_explicit_typed_and_source_traceable(self):
        with TemporaryDirectory() as temporary:
            catalog = DataCatalog(temporary)
            product = DataProductDefinition(
                DatasetKey("market.trades.crypto.btc_usd"), "BTC spot trades", DatasetLayer.CANONICAL,
                sources=(SourceBinding("vendor-a", "binance", 100), SourceBinding("vendor-b", "deribit", 90)),
            ); catalog.register_product(product)
            for release_id, provider, venue, currency, price in (
                ("source-a", "vendor-a", "binance", "USDT", 100),
                ("source-b", "vendor-b", "deribit", "USD", 200),
            ):
                directory = Path(temporary) / "canonical" / release_id
                manifest = write_event_dataset(directory, [{
                    "event_time": "2026-01-01T00:00:00Z", "available_time": "2026-01-01T00:00:00Z",
                    "instrument_id": "BTC", "trade_id": release_id, "price": price, "size": 1,
                }], dataset_id=release_id, schema={"schema_id": "market.trade.v1", "primary_key": ["trade_id"]},
                   lineage={"source": {"provider": provider, "venue": venue}})
                catalog.register_release(DatasetRelease(
                    release_id, product.key, "1", "market.trade", "1", "test", "1",
                    f"canonical/{release_id}", "parquet", str(manifest["dataset_sha256"]), provider, venue,
                ))
            catalog.save()
            release = ConsolidatedTradeBuilder(temporary).build(
                "curated.consolidated_trades.crypto.btc_usd", "BTC consolidated spot trades", (
                    ConsolidatedTradeInput(product, "vendor-a", "binance", "spot", "USDT"),
                    ConsolidatedTradeInput(product, "vendor-b", "deribit", "spot", "USD"),
                ), ConsolidatedTradePolicy("btc_spot_union", "1", "USD", {"USDT": Decimal("1"), "USD": Decimal("1")}),
                start="2026-01-01T00:00:00Z", end="2026-01-02T00:00:00Z",
            )
            rows = ResearchDataClient(temporary).get(release.release_id).collect("rows")
            self.assertEqual({row["venue"] for row in rows}, {"binance", "deribit"})
            self.assertEqual({row["source_release_id"] for row in rows}, {"source-a", "source-b"})
            with self.assertRaisesRegex(ValueError, "cannot be mixed"):
                ConsolidatedTradeBuilder(temporary).build(
                    "curated.invalid", "Invalid", (
                        ConsolidatedTradeInput(product, "vendor-a", "binance", "spot", "USDT"),
                        ConsolidatedTradeInput(product, "vendor-b", "deribit", "perpetual", "USD"),
                    ), ConsolidatedTradePolicy("invalid", "1", "USD", {"USDT": Decimal("1"), "USD": Decimal("1")}),
                    start="2026-01-01T00:00:00Z", end="2026-01-02T00:00:00Z",
                )

    def test_missing_data_plan_is_explicit_and_backtest_cannot_acquire(self):
        class Connector:
            provider = "test-provider"

            def __init__(self):
                self.requests = []

            def supports(self, logical_key):
                return logical_key == "market.trades.crypto.test"

            def acquire(self, request):
                self.requests.append(request)
                return DatasetRelease("ds_test_1", DatasetKey(request.logical_key), "1", "market.trade", "1",
                    "test", "1", "canonical/test", "parquet", provider=self.provider)

        with TemporaryDirectory() as temporary:
            catalog = DataCatalog(temporary)
            product = DataProductDefinition(DatasetKey("market.trades.crypto.test"), "Test trades", DatasetLayer.CANONICAL,
                                     sources=(SourceBinding("test-provider", "test", 100),))
            catalog.register_product(product); catalog.save()
            connector = Connector(); providers = ProviderRegistry(); providers.register(connector)
            start, end = NOW, NOW + timedelta(hours=1)
            client = ResearchDataClient(temporary, providers=providers)
            plan = client.plan(product.key, start=start, end=end)
            self.assertFalse(plan.complete)
            self.assertEqual(plan.selected.provider, "test-provider")
            self.assertTrue(plan.connector_available)
            self.assertEqual(plan.estimate.requests, 1)
            with self.assertRaisesRegex(DataUnavailableError, "kairos data acquire"):
                client.get(product.key, start=start, end=end, acquire=AcquirePolicy.PLAN)
            release = client.acquire(plan)
            self.assertEqual(release.release_id, "ds_test_1")
            self.assertEqual(len(connector.requests), 1)
            backtest = ResearchDataClient(temporary, providers=providers, run_mode=RunMode.BACKTEST)
            with self.assertRaisesRegex(RuntimeError, "forbids data acquisition"):
                backtest.acquire(plan)

    def test_acquisition_limits_fail_before_provider_network_call(self):
        class Archive:
            called = False

            def fetch_daily(self, *args):
                self.called = True
                return {}

        with TemporaryDirectory() as temporary:
            catalog = DataCatalog(temporary); catalog.register_product(BTC_SPOT_DAILY.product); catalog.save()
            archive = Archive(); providers = ProviderRegistry(); providers.register(BinanceSpotDatasetConnector(temporary, archive))
            client = ResearchDataClient(
                temporary, providers=providers, acquisition_limits=AcquisitionLimits(maximum_requests=1),
            )
            with self.assertRaisesRegex(RuntimeError, "estimates 2 requests"):
                client.get(BTC_SPOT_DAILY.product, start="2026-01-01T00:00:00Z", end="2026-01-03T00:00:00Z",
                           acquire=AcquirePolicy.IF_MISSING)
            self.assertFalse(archive.called)

    def test_coverage_plan_preserves_internal_missing_ranges(self):
        with TemporaryDirectory() as temporary:
            product = DataProductDefinition(
                DatasetKey("market.ohlcv.test.gapped"), "Gapped prices", DatasetLayer.CANONICAL,
                primary_time="period_start", sources=(SourceBinding("test-provider", "test", 100),),
            )
            directory = Path(temporary) / "canonical" / "gapped"
            manifest = write_daily_dataset(
                directory, [
                    {"period_start": "2026-01-01T00:00:00Z", "close": 1},
                    {"period_start": "2026-01-03T00:00:00Z", "close": 3},
                ], dataset_id="gapped-v1", schema={"schema_id": "market.ohlcv.v1", "primary_key": ["period_start"]},
                lineage={"source": {"provider": "test-provider", "venue": "test"}},
            )
            catalog = DataCatalog(temporary); catalog.register_product(product)
            catalog.register_release(DatasetRelease(
                "gapped-v1", product.key, "1", "market.ohlcv", "1", "test", "1", "canonical/gapped",
                "parquet", str(manifest["dataset_sha256"]), "test-provider", "test",
            )); catalog.save()
            plan = ResearchDataClient(temporary).plan(
                product, start=datetime(2026, 1, 1, tzinfo=timezone.utc),
                end=datetime(2026, 1, 4, tzinfo=timezone.utc),
            )
            self.assertEqual(plan.missing, (TimeRange(
                datetime(2026, 1, 2, tzinfo=timezone.utc), datetime(2026, 1, 3, tzinfo=timezone.utc),
            ),))

    def test_content_addressed_release_is_quality_gated_and_idempotent(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("pyarrow optional dependency is not installed")
        with TemporaryDirectory() as temporary:
            rows = [{"period_start": "2026-01-01T00:00:00Z", "close": "100"}]
            release_id = content_release_id(BTC_SPOT_DAILY, rows)
            target = Path(temporary) / release_path(BTC_SPOT_DAILY, release_id)
            manifest = write_daily_dataset(
                target, rows, dataset_id=release_id,
                schema={"schema_id": BTC_SPOT_DAILY.schema_id, "primary_key": ["period_start"]},
                lineage={"source": {"provider": "binance"}},
            )
            first = publish_release(
                temporary, BTC_SPOT_DAILY, release_id, manifest, provider="binance", venue="binance",
                transform_id="binance.spot.ohlcv", transform_version="1", quality_level=QualityLevel.BACKTEST,
            )
            second = publish_release(
                temporary, BTC_SPOT_DAILY, release_id, manifest, provider="binance", venue="binance",
                transform_id="binance.spot.ohlcv", transform_version="1", quality_level=QualityLevel.BACKTEST,
            )
            self.assertEqual(first, second)
            self.assertEqual(DataCatalog(temporary).release(BTC_SPOT_DAILY.key).release_id, release_id)
            self.assertTrue((target / "release.json").exists())
            self.assertTrue((target / "usage.json").exists())
            query = ResearchDataClient(temporary).get(BTC_SPOT_DAILY.product, fields=("period_start", "close"))
            snapshot_path = Path(temporary) / "studies" / "example" / "data_snapshot.json"
            ResearchDataClient.freeze_study(snapshot_path, "example", (query,), code_version="test-commit")
            snapshot = json.loads(snapshot_path.read_text())
            self.assertEqual(snapshot["inputs"][0]["release_id"], release_id)
            self.assertEqual(snapshot["inputs"][0]["content_hash"], manifest["dataset_sha256"])
            self.assertEqual(snapshot["inputs"][0]["source_policy_version"], "priority-v1")

    def test_if_missing_acquires_publishes_and_queries_through_provider_connector(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("pyarrow optional dependency is not installed")

        class Archive:
            def __init__(self):
                self.calls = []

            def fetch_daily(self, symbol, start, end, source_root):
                self.calls.append((symbol, start, end))
                return {start + timedelta(days=offset):
                        {"open": 100 + offset, "high": 110 + offset, "low": 90 + offset,
                         "close": 105 + (start - date(2026, 1, 1)).days + offset, "volume": 5}
                        for offset in range((end - start).days + 1)}

        with TemporaryDirectory() as temporary:
            catalog = DataCatalog(temporary); catalog.register_product(BTC_SPOT_DAILY.product); catalog.save()
            archive = Archive(); providers = ProviderRegistry()
            providers.register(BinanceSpotDatasetConnector(temporary, archive))
            client = ResearchDataClient(temporary, providers=providers)
            rows = client.get(
                BTC_SPOT_DAILY.product, start="2026-01-01T00:00:00Z", end="2026-01-02T00:00:00Z",
                fields=("period_start", "close"), acquire=AcquirePolicy.IF_MISSING,
            ).collect(OutputFormat.ROWS)
            self.assertEqual(rows, [{"period_start": "2026-01-01T00:00:00Z", "close": 105}])
            self.assertEqual(archive.calls, [("BTCUSDT", date(2026, 1, 1), date(2026, 1, 1))])
            release = DataCatalog(temporary).release(BTC_SPOT_DAILY.key)
            self.assertEqual(release.provider, "binance")
            self.assertEqual(release.quality_level, QualityLevel.BACKTEST)
            client.get(
                BTC_SPOT_DAILY.product, start="2026-01-02T00:00:00Z", end="2026-01-03T00:00:00Z",
                acquire=AcquirePolicy.IF_MISSING,
            )
            combined = client.get(
                BTC_SPOT_DAILY.product, start="2026-01-01T00:00:00Z", end="2026-01-03T00:00:00Z",
                fields=("period_start", "close"),
            ).collect(OutputFormat.ROWS)
            self.assertEqual([row["close"] for row in combined], [105, 106])
            self.assertEqual(len(DataCatalog(temporary).releases(BTC_SPOT_DAILY.key)), 2)

    def test_massive_events_are_exposed_as_typed_columns_and_sql(self):
        try:
            import duckdb  # noqa: F401
        except ImportError:
            self.skipTest("query optional dependency is not installed")
        with TemporaryDirectory() as temporary:
            event = MarketEventEnvelope(InstrumentId("option:us:test"), NOW, NOW, NOW + timedelta(seconds=1),
                "massive", "options.quotes", "O:TEST", MarketEventType.QUOTE, 0,
                {"bid": Decimal("1.25"), "ask": Decimal("1.50"), "bid_size": Decimal("10")})
            manifest = ParquetMarketEventRepository(f"{temporary}/canonical/market").write_batch(
                "quotes.test.v1", (event,), lineage={"request_window": {
                    "start": NOW.isoformat(), "end": (NOW + timedelta(seconds=1)).isoformat()}})
            catalog = DataCatalog(temporary)
            product = DataProductDefinition(DatasetKey("market.option_quotes.us.sql_test"), "SQL test quotes", DatasetLayer.CANONICAL)
            catalog.register_product(product)
            catalog.register_release(DatasetRelease(
                "quotes.test.v1", product.key, "1", "market.event_envelope", "1", "test", "1",
                "canonical/market/dataset=quotes.test.v1", "parquet", str(manifest["dataset_sha256"]),
                "massive", "opra", (), DatasetStatus.APPROVED_FOR_RESEARCH, QualityLevel.RESEARCH,
                storage_kind=DatasetStorageKind.MARKET_EVENTS,
            )); catalog.save()
            client = ResearchDataClient(temporary)
            table = client.get("quotes.test.v1", start=NOW, end=NOW + timedelta(seconds=1),
                               fields=("instrument_id", "bid", "ask")).collect(OutputFormat.ARROW)
            self.assertEqual(table.column_names, ["instrument_id", "bid", "ask"])
            result = client.sql("select avg(ask - bid) as spread from quotes",
                                datasets={"quotes": "quotes.test.v1"}, output="rows")
            self.assertEqual(result[0]["spread"], Decimal("0.25"))

    def test_replay_freezes_release_hash_and_is_deterministic(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("pyarrow optional dependency is not installed")
        with TemporaryDirectory() as temporary:
            events = tuple(MarketEventEnvelope(
                InstrumentId("option:us:test"), NOW + timedelta(seconds=index),
                NOW + timedelta(seconds=index), NOW + timedelta(seconds=index + 1),
                "massive", "options.quotes", "O:TEST", MarketEventType.QUOTE, index,
                {"bid": Decimal("1.25"), "ask": Decimal("1.50")},
            ) for index in range(2))
            manifest = ParquetMarketEventRepository(f"{temporary}/canonical/market").write_batch(
                "quotes.replay.v1", events, lineage={"request_window": {
                    "start": NOW.isoformat(), "end": (NOW + timedelta(seconds=2)).isoformat()}})
            catalog = DataCatalog(temporary)
            product = DataProductDefinition(DatasetKey("market.option_quotes.us.test"), "Test quotes", DatasetLayer.CANONICAL)
            catalog.register_product(product)
            catalog.register_release(DatasetRelease(
                "quotes.replay.v1", product.key, "1", "market.event_envelope", "1", "massive.quotes", "1",
                "canonical/market/dataset=quotes.replay.v1", "parquet", str(manifest["dataset_sha256"]),
                "massive", "opra", ("quotes@research",), DatasetStatus.APPROVED_FOR_BACKTEST,
                QualityLevel.BACKTEST, storage_kind=DatasetStorageKind.MARKET_EVENTS,
            )); catalog.save()
            client = ResearchDataClient(temporary, run_mode=RunMode.BACKTEST)
            feed = client.replay("quotes@research", NOW, NOW + timedelta(seconds=2))
            first = tuple(item.event_key for item in feed)
            second = tuple(item.event_key for item in feed)
            self.assertEqual(first, second)
            self.assertEqual(feed.release_id, "quotes.replay.v1")
            self.assertEqual(feed.content_hash, manifest["dataset_sha256"])

    def test_market_snapshot_replay_feed_is_hash_checked(self):
        with TemporaryDirectory() as temporary:
            dataset = build_synthetic_backtest_dataset()
            MarketSnapshotStorageDriver(Path(temporary) / "datasets").save(dataset)
            catalog = DataCatalog(temporary)
            product = DataProductDefinition(DatasetKey("curated.option_chain.us.synthetic"), "Synthetic option chain", DatasetLayer.CURATED)
            catalog.register_product(product)
            catalog.register_release(DatasetRelease(
                dataset.manifest.dataset_id, product.key, "1", "market_replay_dataset.v2", "2", "synthetic", "1",
                f"datasets/{dataset.manifest.dataset_id}", "parquet", dataset.manifest.content_hash,
                "synthetic", "synthetic", (), DatasetStatus.APPROVED_FOR_BACKTEST, QualityLevel.BACKTEST,
                storage_kind=DatasetStorageKind.MARKET_SNAPSHOTS,
            )); catalog.save()
            feed = ResearchDataClient(temporary, run_mode=RunMode.BACKTEST).replay_snapshots(product)
            first = tuple(item.timestamp for item in feed.between(dataset.manifest.start, dataset.manifest.end))
            second = tuple(item.timestamp for item in feed.between(dataset.manifest.start, dataset.manifest.end))
            self.assertEqual(first, second)
            self.assertEqual(feed.content_hash, dataset.manifest.content_hash)

    def test_run_modes_enforce_release_promotion_and_quality(self):
        with TemporaryDirectory() as temporary:
            catalog = DataCatalog(temporary)
            product = DataProductDefinition(DatasetKey("market.events.test"), "Test events", DatasetLayer.CANONICAL)
            catalog.register_product(product)
            catalog.register_release(DatasetRelease(
                "research-only", product.key, "1", "market.event", "1", "test", "1",
                "canonical/market/dataset=research-only", "parquet", "hash", status=DatasetStatus.APPROVED_FOR_RESEARCH,
                quality_level=QualityLevel.RESEARCH,
            )); catalog.save()
            with self.assertRaisesRegex(PermissionError, "approved_for_backtest"):
                ResearchDataClient(temporary, run_mode=RunMode.BACKTEST).get(product)
            with self.assertRaisesRegex(PermissionError, "approved_for_production"):
                ResearchDataClient(temporary, run_mode=RunMode.LIVE).get(product)

    def test_release_promotion_is_quality_gated_and_audited(self):
        with TemporaryDirectory() as temporary:
            catalog = DataCatalog(temporary)
            product = DataProductDefinition(DatasetKey("market.events.promote"), "Promotion test", DatasetLayer.CANONICAL)
            catalog.register_product(product)
            catalog.register_release(DatasetRelease(
                "promote-v1", product.key, "1", "market.event", "1", "test", "1", "canonical/test",
                "parquet", "hash", status=DatasetStatus.APPROVED_FOR_RESEARCH,
                quality_level=QualityLevel.BACKTEST,
            )); catalog.save()
            promoted = catalog.promote(
                "promote-v1", DatasetStatus.APPROVED_FOR_BACKTEST, actor="test", reason="quality review passed",
            )
            self.assertEqual(promoted.status, DatasetStatus.APPROVED_FOR_BACKTEST)
            audit = (Path(temporary) / "catalog" / "promotions.jsonl").read_text()
            self.assertIn("quality review passed", audit)
            with self.assertRaisesRegex(ValueError, "requires a higher quality"):
                low = DatasetRelease("low", product.key, "2", "market.event", "1", "test", "1", "canonical/low",
                    "parquet", "hash2", status=DatasetStatus.APPROVED_FOR_RESEARCH,
                    quality_level=QualityLevel.RESEARCH)
                catalog.register_release(low)
                catalog.promote("low", DatasetStatus.APPROVED_FOR_BACKTEST, actor="test", reason="not enough")

    def test_alias_promotion_moves_pointer_without_mutating_releases_and_is_audited(self):
        with TemporaryDirectory() as temporary:
            catalog = DataCatalog(temporary)
            product = DataProductDefinition(DatasetKey("market.events.alias-test"), "Alias test", DatasetLayer.CANONICAL)
            catalog.register_product(product)
            releases = []
            for version in ("1", "2"):
                release = DatasetRelease(
                    f"alias-v{version}", product.key, version, "event", "1", "test", version,
                    f"canonical/v{version}", "parquet", f"hash-{version}", status=DatasetStatus.APPROVED_FOR_RESEARCH,
                )
                catalog.register_release(release); releases.append(release)
            alias = f"{product.key}@research"
            catalog.promote_alias(alias, releases[0].release_id, actor="reviewer", reason="initial approval",
                                  quality_report_hash="quality-1")
            catalog.promote_alias(alias, releases[1].release_id, actor="reviewer", reason="new release approved",
                                  quality_report_hash="quality-2")
            loaded = DataCatalog(temporary)
            self.assertEqual(loaded.release(alias).release_id, "alias-v2")
            self.assertEqual(loaded.release("alias-v1"), releases[0])
            records = [json.loads(line) for line in
                       (Path(temporary) / "catalog" / "alias-promotions.jsonl").read_text().splitlines()]
            self.assertEqual(records[-1]["from_release"], "alias-v1")
            self.assertEqual(records[-1]["to_release"], "alias-v2")
            self.assertEqual(records[-1]["quality_report_hash"], "quality-2")


if __name__ == "__main__":
    unittest.main()
