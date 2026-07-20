from __future__ import annotations

import asyncio
import json
from pathlib import Path
from threading import Event
import tempfile
import unittest

from kairos.application import ApplicationConfig, AsyncKairosRuntime, KairosApplication, RuntimePaths, runtime_feed_plan
from kairos.connectors.binance import BinanceRuntimeFeedFactory
from kairos.data import (
    DataCatalog,
    DataSetContractArtifact,
    LiveViewManifest,
    PAPER_LIVE_FRESHNESS_POLICY,
    register_live_capture_release,
    evaluate_live_view_freshness,
    live_view_manifest_path,
    load_live_view_manifest,
    write_live_view_manifest,
)
from kairos.data.products import BTC_SPOT_DAILY
from kairos.ports import Environment
from kairos.orchestration.runtime_store import SQLiteRuntimeStore


class OneMessageConnection:
    def __init__(self, message: dict[str, object]) -> None:
        self.messages = [message]
        self.closed = Event()

    def receive(self):
        if self.messages:
            return self.messages.pop(0)
        self.closed.wait(5)
        return ""

    def close(self) -> None:
        self.closed.set()


class FixtureConnector:
    def __init__(self, message: dict[str, object]) -> None:
        self.message = message
        self.urls: list[str] = []
        self.connections: list[OneMessageConnection] = []

    def connect(self, url: str) -> OneMessageConnection:
        self.urls.append(url)
        connection = OneMessageConnection(self.message)
        self.connections.append(connection)
        return connection


class BinanceRuntimeFeedFactoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_factory_builds_supervised_binance_feed_and_freshness_monitor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset_id = str(BTC_SPOT_DAILY.key)
            live_view_id = "live:binance:btcusdt-book"
            manifest_path = live_view_manifest_path(root, dataset_id, live_view_id)
            write_live_view_manifest(manifest_path, LiveViewManifest(
                dataset_id,
                live_view_id,
                DataSetContractArtifact.from_product_contract(BTC_SPOT_DAILY).contract_hash,
                "connector-hash",
                "available_time",
                ("available_time", "bid", "ask"),
                {
                    "provider": "binance",
                    "event_source_contract": "EventSource[DataSetRecord]",
                    "channel_contract": "BoundedEventChannel",
                    "freshness": {"max_age_seconds": 60},
                    "channel_capacity": 8,
                },
                {
                    "kind": "binance_market_stream",
                    "provider": "binance",
                    "symbol": "BTCUSDT",
                    "channel": "bookTicker",
                    "instrument_id": "crypto:binance:spot:BTCUSDT",
                    "public_only": True,
                },
                "configured",
                "2026-07-20T00:00:00+00:00",
            ))
            plan = runtime_feed_plan("paper", ({
                "name": "bars",
                "dataset": dataset_id,
                "live_view_id": live_view_id,
                "event_source_contract": "EventSource[DataSetRecord]",
                "channel_contract": "BoundedEventChannel",
                "freshness_gate": {"passed": True},
            },))
            connector = FixtureConnector({
                "e": "bookTicker",
                "s": "BTCUSDT",
                "b": "100.0",
                "a": "101.0",
                "B": "1.5",
                "A": "2.0",
                "u": 42,
                "E": 1767225600000,
            })
            feed = BinanceRuntimeFeedFactory(
                root,
                connector=connector,
                environment=Environment.LIVE,
                monitor_interval_seconds=0.01,
                journal_root=root / "journals",
            ).build(plan)

            paths = RuntimePaths.under(root / "runtime")
            app = KairosApplication(
                ApplicationConfig(Environment.PAPER, paths),
                SQLiteRuntimeStore(paths.runtime_database),
                runtime_id="binance-runtime-feed",
            )
            runtime = AsyncKairosRuntime(app, feed.managed_services)
            await runtime.start()
            await asyncio.sleep(0.05)
            await runtime.stop()

            service = feed.connector_services["feed:bars:live:binance:btcusdt-book"]
            updated = evaluate_live_view_freshness(
                load_live_view_manifest(manifest_path),
                policy=PAPER_LIVE_FRESHNESS_POLICY,
            )
            journal_paths = sorted(
                path for path in (root / "journals").glob("*.jsonl")
                if not path.name.endswith(".canonical.jsonl")
            )
            capture_manifests = sorted((root / "journals").glob("*.rotation.manifest.json"))
            raw_journal_symbol = json.loads(
                journal_paths[0].read_text(encoding="utf-8").splitlines()[0],
            )["s"]
            release = register_live_capture_release(
                root,
                dataset_id=dataset_id,
                capture_manifest_path=capture_manifests[0],
                run_id="run_fixture",
                live_view_id=live_view_id,
                provider="binance",
            )
            release_manifest_path = root / release.relative_path / "data_release_manifest.json"
            release_manifest = json.loads(release_manifest_path.read_text(encoding="utf-8"))
            catalog_release = DataCatalog(root).release(release.release_id)

        self.assertTrue(connector.urls[0].endswith("/btcusdt@bookTicker"))
        self.assertEqual(service.raw_messages, 1)
        self.assertEqual(service.canonical_events, 1)
        self.assertTrue(updated.passed)
        self.assertEqual(updated.freshness_status, "healthy")
        self.assertEqual(updated.channel_diagnostics["dropped"], 0)
        self.assertEqual(feed.runtime_bundle.manifest()["feed_service_ids"], ["feed:bars:live:binance:btcusdt-book"])
        self.assertEqual(feed.runtime_bundle.manifest()["monitor_service_ids"], ["feed-monitor:bars:live:binance:btcusdt-book"])
        self.assertEqual(len(feed.runtime_bundle.bundle_hash), 64)
        self.assertEqual(len(journal_paths), 1)
        self.assertEqual(raw_journal_symbol, "BTCUSDT")
        self.assertEqual(len(capture_manifests), 1)
        self.assertEqual(catalog_release.content_hash, release.content_hash)
        self.assertEqual(catalog_release.storage_kind.value, "market_events")
        self.assertEqual(release_manifest["kind"], "data_release_manifest")
        self.assertEqual(release_manifest["dataset_id"], dataset_id)
        self.assertEqual(release_manifest["source"]["run_id"], "run_fixture")

    def test_factory_rejects_non_binance_live_view_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset_id = str(BTC_SPOT_DAILY.key)
            live_view_id = "live:custom"
            manifest_path = live_view_manifest_path(root, dataset_id, live_view_id)
            write_live_view_manifest(manifest_path, LiveViewManifest(
                dataset_id,
                live_view_id,
                DataSetContractArtifact.from_product_contract(BTC_SPOT_DAILY).contract_hash,
                "connector-hash",
                "available_time",
                ("available_time", "close"),
                {"channel_contract": "BoundedEventChannel", "freshness": {"max_age_seconds": 60}},
                {"kind": "live_connector", "name": "custom.py"},
                "configured",
                "2026-07-20T00:00:00+00:00",
            ))
            plan = runtime_feed_plan("paper", ({
                "name": "bars",
                "dataset": dataset_id,
                "live_view_id": live_view_id,
                "event_source_contract": "EventSource[DataSetRecord]",
                "channel_contract": "BoundedEventChannel",
                "freshness_gate": {"passed": True},
            },))

            with self.assertRaisesRegex(ValueError, "not a Binance"):
                BinanceRuntimeFeedFactory(root, connector=FixtureConnector({})).build(plan)


if __name__ == "__main__":
    unittest.main()
