from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

from examples.backtest.governed_sma import canonical_events, fixture_bars
from kairos.application import builtin_runtime_strategy_model_registry
from kairos.execution.order_state import DurableOrderStatus
from kairos.market_data import BoundedEventChannel
from kairos.orchestration.runtime_store import SQLiteRuntimeStore
from kairos.product_surface import DataProductApi, RunProductApi, StrategyProductApi, StudyProductApi


ROOT = Path(__file__).parents[1]


def command(root: Path, *args: str) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-m", "kairos", "--format", "json", "--lake-root", str(root), *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


class FourProductSurfaceTests(unittest.TestCase):
    def test_builtin_strategy_runtime_model_registry_resolves_aliases(self) -> None:
        registry = builtin_runtime_strategy_model_registry()

        self.assertEqual(registry.resolve("sma-cross-v1").kind, "sma-cross-v1")
        self.assertEqual(registry.resolve("builtin.sma-cross-v1").kind, "sma-cross-v1")
        with self.assertRaisesRegex(ValueError, "registered=.*sma-cross-v1"):
            registry.resolve("unknown-strategy")

    def test_paper_run_requires_healthy_live_view_freshness(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external = root / "external-input"
            external.mkdir()
            contract = external / "sentiment.contract.json"
            csv_file = external / "sentiment.csv"
            live_connector = external / "sentiment_live.py"

            contract.write_text(json.dumps({
                "dataset_id": "reference.sentiment.equity.us",
                "primary_time": "available_time",
                "grain": {"kind": "event_stream"},
                "fields": ["available_time", "instrument_id", "sentiment"],
                "freshness": {"max_age_seconds": 60},
            }), encoding="utf-8")
            csv_file.write_text(
                "available_time,instrument_id,sentiment\n"
                "2026-01-01T00:00:00Z,equity:US:AAPL,0.4\n",
                encoding="utf-8",
            )
            live_connector.write_text(
                "def subscribe(params, context):\n"
                "    yield {'available_time': '2026-01-01T00:00:00Z', 'instrument_id': 'equity:US:AAPL', 'sentiment': 0.4}\n",
                encoding="utf-8",
            )

            data = DataProductApi(root)
            study = StudyProductApi(root)
            strategy = StrategyProductApi(root)
            run = RunProductApi(root)

            data.write_file(csv_file, as_dataset="reference.sentiment.equity.us", contract=contract)
            live_view = data.write_live(live_connector, as_dataset="reference.sentiment.equity.us", contract=contract)
            study.open("live-freshness-study", hypothesis="fresh live data is required")
            study.add_data("live-freshness-study", name="sentiment", dataset="reference.sentiment.equity.us")
            study.freeze("live-freshness-study", version="1.0.0")
            strategy.open("live-freshness-strategy", from_study="live-freshness-study@1.0.0")
            strategy_lock = strategy.freeze("live-freshness-strategy", version="1.0.0")

            with self.assertRaisesRegex(ValueError, "paper-live-freshness"):
                run.start_snapshot("live-freshness-strategy@1.0.0", mode="paper")

            manifest_path = Path(str(live_view["artifact"]))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["freshness_status"] = "healthy"
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "missing_channel_diagnostics"):
                run.start_snapshot("live-freshness-strategy@1.0.0", mode="paper")

            manifest["live_data_plane"]["channel_diagnostics"] = {
                "capacity": 64,
                "peak_depth": 1,
                "dropped": 0,
                "sequence_gaps": 0,
                "conflated": 0,
                "reconnects": 0,
            }
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            started = run.start_snapshot("live-freshness-strategy@1.0.0", mode="paper")

        self.assertEqual(started["target"]["hash"], strategy_lock["lock_hash"])
        feed = started["runtime_contract"]["feed_bindings"][0]
        self.assertEqual(feed["name"], "sentiment")
        self.assertEqual(feed["event_source_contract"], "EventSource[DataSetRecord]")
        self.assertEqual(feed["channel_contract"], "BoundedEventChannel")
        self.assertTrue(feed["freshness_gate"]["passed"])
        composition = started["runtime_contract"]["run_mode_composition"]
        self.assertEqual(composition["mode"], "paper-trading")
        self.assertEqual(composition["event_source"], "live:binance")
        self.assertEqual(composition["execution_driver"], "simulated")
        self.assertEqual(composition["capture_policy"], "raw_and_canonical")
        self.assertEqual(len(composition["composition_hash"]), 64)
        execution_plan = started["runtime_contract"]["execution_runtime_plan"]
        self.assertEqual(execution_plan["services"][0]["service_id"], "execution:paper-trading:simulated")
        self.assertEqual(execution_plan["services"][0]["execution_driver"], "simulated")
        self.assertEqual(len(execution_plan["plan_hash"]), 64)
        strategy_plan = started["runtime_contract"]["strategy_runtime_plan"]
        self.assertEqual(strategy_plan["services"][0]["service_id"], "strategy:paper-trading:live-freshness-strategy")
        self.assertEqual(strategy_plan["services"][0]["strategy_id"], "live-freshness-strategy")
        self.assertEqual(strategy_plan["services"][0]["target_hash"], strategy_lock["lock_hash"])
        self.assertEqual(len(strategy_plan["plan_hash"]), 64)
        plan = started["runtime_contract"]["feed_runtime_plan"]
        self.assertEqual(plan["services"][0]["service_id"], f"feed:sentiment:{feed['live_view_id']}")
        self.assertEqual(plan["services"][0]["live_view_id"], feed["live_view_id"])
        self.assertEqual(plan["services"][0]["capture_policy"], "raw_and_canonical")
        self.assertEqual(len(plan["plan_hash"]), 64)
        bundle = started["runtime_contract"]["feed_runtime_bundle"]
        self.assertEqual(bundle["plan_hash"], plan["plan_hash"])
        self.assertEqual(bundle["feed_service_ids"], [f"feed:sentiment:{feed['live_view_id']}"])
        self.assertEqual(bundle["monitor_service_ids"], [f"feed-monitor:sentiment:{feed['live_view_id']}"])
        self.assertEqual(len(bundle["bundle_hash"]), 64)
        freshness = started["runtime_contract"]["freshness_gates"][0]
        self.assertTrue(freshness["passed"])
        self.assertEqual(freshness["freshness_status"], "healthy")
        self.assertEqual(freshness["max_age_seconds"], 60)
        self.assertEqual(freshness["channel_failures"], [])
        self.assertEqual(freshness["channel_diagnostics"]["dropped"], 0)

    def test_live_data_write_requires_freshness_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external = root / "external-input"
            external.mkdir()
            contract = external / "quotes.contract.json"
            live_connector = external / "quotes_live.py"
            contract.write_text(json.dumps({
                "dataset_id": "market.quotes.equity.us",
                "primary_time": "available_time",
                "grain": {"kind": "event_stream"},
                "fields": ["available_time", "instrument_id", "bid", "ask"],
            }), encoding="utf-8")
            live_connector.write_text("def subscribe(params, context):\n    yield {}\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "freshness.max_age_seconds"):
                DataProductApi(root).write_live(
                    live_connector, as_dataset="market.quotes.equity.us", contract=contract,
                )

            payload = json.loads(contract.read_text(encoding="utf-8"))
            payload["freshness"] = {"max_age_seconds": "30"}
            contract.write_text(json.dumps(payload), encoding="utf-8")

            live_view = DataProductApi(root).write_live(
                live_connector, as_dataset="market.quotes.equity.us", contract=contract,
            )

        self.assertEqual(live_view["live_data_plane"]["freshness"]["max_age_seconds"], 30)

    def test_paper_run_can_execute_injected_feed_runtime_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external = root / "external-input"
            external.mkdir()
            contract = external / "sentiment.contract.json"
            csv_file = external / "sentiment.csv"
            live_connector = external / "sentiment_live.py"
            contract.write_text(json.dumps({
                "dataset_id": "reference.sentiment.equity.us",
                "primary_time": "available_time",
                "grain": {"kind": "event_stream"},
                "fields": ["available_time", "instrument_id", "sentiment"],
                "freshness": {"max_age_seconds": 60},
            }), encoding="utf-8")
            csv_file.write_text(
                "available_time,instrument_id,sentiment\n"
                "2026-01-01T00:00:00Z,equity:US:AAPL,0.4\n",
                encoding="utf-8",
            )
            live_connector.write_text(
                "def subscribe(params, context):\n"
                "    yield {'available_time': '2026-01-01T00:00:00Z', 'instrument_id': 'equity:US:AAPL', 'sentiment': 0.4}\n",
                encoding="utf-8",
            )
            data = DataProductApi(root)
            study = StudyProductApi(root)
            strategy = StrategyProductApi(root)
            run = RunProductApi(root)
            data.write_file(csv_file, as_dataset="reference.sentiment.equity.us", contract=contract)
            live_view = data.write_live(live_connector, as_dataset="reference.sentiment.equity.us", contract=contract)
            study.open("runtime-feed-study", hypothesis="paper runtime consumes live feed")
            study.add_data("runtime-feed-study", name="sentiment", dataset="reference.sentiment.equity.us")
            study.freeze("runtime-feed-study", version="1.0.0")
            strategy.open("runtime-feed-strategy", from_study="runtime-feed-study@1.0.0")
            strategy.freeze("runtime-feed-strategy", version="1.0.0")
            manifest_path = Path(str(live_view["artifact"]))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["freshness_status"] = "healthy"
            manifest["live_data_plane"]["channel_diagnostics"] = {
                "capacity": 64,
                "peak_depth": 1,
                "dropped": 0,
                "sequence_gaps": 0,
                "conflated": 0,
                "reconnects": 0,
            }
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            def fake_factory(plan):
                async def run_forever():
                    await asyncio.Event().wait()

                bundle = plan.managed_service_bundle(
                    feed_runner_factory=lambda _service: run_forever,
                    monitor_runner_factory=lambda _service: run_forever,
                )
                return SimpleNamespace(
                    runtime_bundle=bundle,
                    managed_services=bundle.services,
                    manifest_paths={"feed:sentiment:" + plan.services[0].live_view_id: manifest_path},
                )

            def fake_strategy_factory(_service):
                async def run_forever():
                    await asyncio.Event().wait()

                return run_forever

            started = run.start_snapshot(
                "runtime-feed-strategy@1.0.0",
                mode="paper",
                execute_feeds=True,
                execute_strategy=True,
                feed_runtime_seconds=0.01,
                feed_runtime_factory=fake_factory,
                strategy_runtime_factory=fake_strategy_factory,
            )

        execution = started["runtime_contract"]["feed_runtime_execution"]
        self.assertTrue(execution["executed"])
        self.assertEqual(execution["provider"], "binance")
        self.assertEqual(len(execution["bundle_hash"]), 64)
        service_statuses = {item["name"]: item["status"] for item in execution["services"]}
        self.assertEqual(service_statuses["feed-monitor:sentiment:" + manifest["live_view_id"]], "running")
        self.assertEqual(service_statuses["feed:sentiment:" + manifest["live_view_id"]], "running")
        self.assertEqual(service_statuses["execution:paper-trading:simulated"], "running")
        self.assertEqual(service_statuses["strategy:paper-trading:runtime-feed-strategy"], "running")
        self.assertEqual(len(execution["execution_plan_hash"]), 64)
        self.assertEqual(len(execution["strategy_plan_hash"]), 64)
        self.assertIn("feed:sentiment:" + manifest["live_view_id"], execution["manifest_paths"])

    def test_paper_run_can_instantiate_builtin_strategy_runner_from_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external = root / "external-input"
            external.mkdir()
            contract = external / "bars.contract.json"
            csv_file = external / "bars.csv"
            live_connector = external / "bars_live.py"
            contract.write_text(json.dumps({
                "dataset_id": "market.crypto.bars.binance.btcusdt",
                "primary_time": "period_end",
                "grain": {"kind": "bar", "interval": "1h"},
                "fields": ["period_start", "period_end", "instrument_id", "open", "high", "low", "close", "volume"],
                "freshness": {"max_age_seconds": 60},
            }), encoding="utf-8")
            csv_file.write_text(
                "period_start,period_end,instrument_id,open,high,low,close,volume\n"
                "2026-01-01T00:00:00Z,2026-01-01T01:00:00Z,crypto:binance:spot:BTCUSDT,99,101,98,100,10\n",
                encoding="utf-8",
            )
            live_connector.write_text("def subscribe(params, context):\n    yield {}\n", encoding="utf-8")

            data = DataProductApi(root)
            study = StudyProductApi(root)
            strategy = StrategyProductApi(root)
            run = RunProductApi(root)

            data.write_file(csv_file, as_dataset="market.crypto.bars.binance.btcusdt", contract=contract)
            live_view = data.write_live(live_connector, as_dataset="market.crypto.bars.binance.btcusdt", contract=contract)
            study.open("runtime-sma-study", hypothesis="paper strategy runner consumes the live bar channel")
            study.add_data("runtime-sma-study", name="bars", dataset="market.crypto.bars.binance.btcusdt")
            study.freeze("runtime-sma-study", version="1.0.0")
            strategy.open("runtime-sma-strategy", from_study="runtime-sma-study@1.0.0")
            strategy.set_model(
                "runtime-sma-strategy",
                kind="sma-cross-v1",
                instrument_id="crypto:binance:spot:BTCUSDT",
                fast_window=5,
                slow_window=15,
                approved_capital="10000",
            )
            strategy_lock = strategy.freeze("runtime-sma-strategy", version="1.0.0")

            manifest_path = Path(str(live_view["artifact"]))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["freshness_status"] = "healthy"
            manifest["live_data_plane"]["channel_diagnostics"] = {
                "capacity": 64,
                "peak_depth": 1,
                "dropped": 0,
                "sequence_gaps": 0,
                "conflated": 0,
                "reconnects": 0,
            }
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            def fake_factory(plan):
                channel = BoundedEventChannel(256)

                async def feed_runner():
                    for event in canonical_events(fixture_bars()):
                        await channel.publish(event)
                    await channel.close()
                    await asyncio.Event().wait()

                async def monitor_runner():
                    await asyncio.Event().wait()

                bundle = plan.managed_service_bundle(
                    feed_runner_factory=lambda _service: feed_runner,
                    monitor_runner_factory=lambda _service: monitor_runner,
                )
                return SimpleNamespace(
                    runtime_bundle=bundle,
                    managed_services=bundle.services,
                    manifest_paths={plan.services[0].service_id: manifest_path},
                    channels={plan.services[0].service_id: channel},
                )

            started = run.start_snapshot(
                "runtime-sma-strategy@1.0.0",
                mode="paper",
                execute_feeds=True,
                execute_strategy=True,
                feed_runtime_seconds=0.1,
                feed_runtime_factory=fake_factory,
            )

            execution = started["runtime_contract"]["feed_runtime_execution"]
            service_statuses = {item["name"]: item["status"] for item in execution["services"]}
            self.assertEqual(service_statuses["strategy:paper-trading:runtime-sma-strategy"], "running")
            binding = execution["strategy_bindings"]["runtime-sma-strategy"]
            result_path = Path(binding["output_path"])
            result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(result["strategy_lock_hash"], strategy_lock["lock_hash"])
            self.assertEqual(result["event_count"], len(fixture_bars()))
            self.assertGreater(result["economic_intents"], 0)
            self.assertEqual(len(result["audit_hash"]), 64)
            bridge = execution["intent_execution_bridge"]
            self.assertTrue(bridge["readiness"]["ready"])
            self.assertEqual(bridge["readiness"]["kind"], "paper_runtime_readiness")
            bridge_path = Path(bridge["output_path"])
            bridge_result = json.loads(bridge_path.read_text(encoding="utf-8"))
            self.assertTrue(bridge_result["readiness"]["ready"])
            self.assertEqual(bridge_result["readiness"]["reconciliation"]["differences"], [])
            filled = [
                item for item in bridge_result["submissions"]
                if item["status"] == "filled"
            ]
            self.assertGreater(len(filled), 0)
            self.assertEqual(bridge["submitted_orders"], len(filled))
            self.assertEqual(bridge["filled_orders"], len(filled))
            self.assertEqual(bridge["durable_executions"], len(filled))
            self.assertEqual(filled[0]["request"]["instructions"]["order_type"], "market")
            self.assertEqual(filled[0]["request"]["account"]["institution_id"]["value"], "simulated")
            self.assertEqual(filled[0]["fill"]["order_id"], filled[0]["request"]["client_order_id"])
            self.assertEqual(filled[0]["fill"]["fee_asset"]["value"], "USDT")
            self.assertTrue(filled[0]["durable"]["committed"])
            self.assertEqual(filled[0]["durable"]["order_status"], "filled")
            runtime_store = SQLiteRuntimeStore(Path(filled[0]["durable"]["runtime_database"]))
            self.assertEqual(
                runtime_store.order(filled[0]["request"]["client_order_id"]).status,
                DurableOrderStatus.FILLED,
            )
            self.assertEqual(len(runtime_store.execution_records()), len(filled))
            self.assertEqual(filled[0]["proof"], "paper simulated fill projection from strategy reference price")

    def test_python_product_apis_share_the_same_artifact_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            factor_file = root / "factor.py"
            factor_file.write_text("def compute(data):\n    return data['bars']\n", encoding="utf-8")

            data = DataProductApi(root)
            study = StudyProductApi(root)
            strategy = StrategyProductApi(root)
            run = RunProductApi(root)

            downloaded = data.download("tutorial-sma-data")
            downloaded_release_manifest = (
                root / "canonical" / "tutorial" / "market" / "ohlcv" /
                "instrument=BTC-USDT" / "interval=1h" / "release=fixture:sma-bars-v1" /
                "data_release_manifest.json"
            )
            downloaded_release_manifest_exists = downloaded_release_manifest.exists()
            downloaded_release_manifest_payload = json.loads(downloaded_release_manifest.read_text(encoding="utf-8"))
            study.open("api-study", hypothesis="api path")
            study.add_data(
                "api-study",
                name="bars",
                dataset="market.ohlcv.crypto.tutorial.btc-usdt.1h",
            )
            added_factor = study.add_factor("api-study", name="momentum_12_1", file=factor_file)
            study_lock = study.freeze("api-study", version="1.0.0")
            strategy.open("api-strategy", from_study="api-study@1.0.0")
            strategy.bind_factor("api-strategy", name="primary", study_factor="momentum_12_1")
            strategy_lock = strategy.freeze("api-strategy", version="1.0.0")
            dataset_ref = data.dataset("market.ohlcv.crypto.tutorial.btc-usdt.1h")
            study_data_ref = study.data("api-study", "bars", version="1.0.0")
            study_factor_ref = study.factor("api-study", "momentum_12_1", version="1.0.0")
            dataset_rows = dataset_ref.rows(columns=("available_time", "close"))
            study_rows = study_data_ref.rows(columns=("available_time", "close"))
            started = run.start("api-strategy@1.0.0", mode="backtest")
            study_started = run.start("api-study", mode="study")
            replayed = run.replay(started["run_id"])

        self.assertEqual(downloaded["release_id"], "fixture:sma-bars-v1")
        self.assertTrue(downloaded_release_manifest_exists)
        self.assertEqual(downloaded_release_manifest_payload["kind"], "data_release_manifest")
        self.assertEqual(len(downloaded["contract_hash"]), 64)
        self.assertEqual(len(downloaded["manifest_hash"]), 64)
        self.assertEqual(study_lock["factors"]["momentum_12_1"]["code_hash"], added_factor["code_hash"])
        self.assertEqual(study_lock["data"]["bars"]["contract_hash"], downloaded["contract_hash"])
        self.assertEqual(study_lock["evidence_chain"]["data"]["bars"]["artifact_ref"], downloaded["artifact_ref"])
        self.assertEqual(strategy_lock["data"]["bars"], study_lock["data"]["bars"])
        self.assertEqual(strategy_lock["inputs"]["primary"]["source_hash"], added_factor["code_hash"])
        self.assertEqual(started["target"]["hash"], strategy_lock["lock_hash"])
        self.assertEqual(started["input_artifacts"]["data"]["bars"]["contract_hash"], downloaded["contract_hash"])
        self.assertEqual(started["input_artifacts"]["inputs"]["primary"]["source_hash"], added_factor["code_hash"])
        self.assertEqual(dataset_ref["artifact_ref"], downloaded["artifact_ref"])
        self.assertEqual(study_data_ref["artifact_ref"], downloaded["artifact_ref"])
        self.assertEqual(len(dataset_rows), 90)
        self.assertEqual(study_rows[0]["close"], dataset_rows[0]["close"])
        self.assertEqual(tuple(study_rows[0]), ("available_time", "close"))
        self.assertEqual(study_factor_ref["code_hash"], added_factor["code_hash"])
        self.assertNotIn("path", study_factor_ref)
        self.assertEqual(study_started["target"]["kind"], "study")
        self.assertTrue(replayed["passed"])

    def test_study_freeze_writes_readiness_gate_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            factor_file = root / "factor.py"
            factor_file.write_text("def compute(inputs, params, context):\n    return inputs['bars']\n", encoding="utf-8")

            data = DataProductApi(root)
            study = StudyProductApi(root)
            data.download("tutorial-sma-data")
            study.open("readiness-study")
            study.add_data(
                "readiness-study",
                name="bars",
                dataset="market.ohlcv.crypto.tutorial.btc-usdt.1h",
            )
            study.add_factor("readiness-study", name="legacy_factor", file=factor_file)
            study_lock = study.freeze("readiness-study", version="1.0.0")
            readiness_path = root / "studies" / "readiness-study" / "locks" / "1.0.0" / "readiness.json"
            readiness_exists = readiness_path.exists()
            readiness_payload = json.loads(readiness_path.read_text(encoding="utf-8"))

        self.assertEqual(study_lock["lifecycle"], "FROZEN")
        self.assertTrue(study_lock["readiness"]["passed"])
        self.assertEqual(study_lock["readiness"]["diagnostics"]["factor_metadata_missing"], ["legacy_factor"])
        self.assertTrue(readiness_exists)
        self.assertEqual(readiness_payload["kind"], "study.readiness")

    def test_study_freeze_requires_declared_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            study = StudyProductApi(root)
            study.open("empty-study")
            with self.assertRaisesRegex(ValueError, "missing_data"):
                study.freeze("empty-study", version="1.0.0")

    def test_registered_local_csv_download_publishes_data_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external = root / "external-input"
            external.mkdir()
            contract = external / "custom-bars.contract.json"
            csv_file = external / "custom-bars.csv"
            spec = external / "custom-bars.download.json"
            contract.write_text(json.dumps({
                "dataset_id": "market.ohlcv.crypto.custom.1h",
                "primary_time": "period_end",
                "grain": {"kind": "bar", "interval": "1h"},
                "fields": ["period_end", "instrument_id", "open", "high", "low", "close", "volume"],
            }), encoding="utf-8")
            csv_file.write_text(
                "period_end,instrument_id,open,high,low,close,volume\n"
                "2026-01-01T00:00:00Z,crypto:BTC-USDT,100,110,90,105,12\n",
                encoding="utf-8",
            )
            spec.write_text(json.dumps({
                "kind": "data.download",
                "key": "custom-bars",
                "dataset_id": "market.ohlcv.crypto.custom.1h",
                "source": {"kind": "local_csv", "path": "custom-bars.csv"},
                "contract": "custom-bars.contract.json",
                "quality": {"minimum": "Q2"},
            }), encoding="utf-8")

            data = DataProductApi(root)
            registered = data.register_download("custom-bars", spec)
            downloaded = data.download("custom-bars")
            added = StudyProductApi(root).open("custom-study")
            bars = StudyProductApi(root).add_data(
                "custom-study",
                name="bars",
                dataset="market.ohlcv.crypto.custom.1h",
            )

        self.assertEqual(registered["operation"], "register-download")
        self.assertEqual(downloaded["operation"], "download")
        self.assertEqual(downloaded["dataset_id"], "market.ohlcv.crypto.custom.1h")
        self.assertEqual(downloaded["primary_time"], "period_end")
        self.assertEqual(len(downloaded["download_spec_hash"]), 64)
        self.assertEqual(len(downloaded["content_hash"]), 64)
        self.assertEqual(len(downloaded["manifest_hash"]), 64)
        self.assertEqual(len(downloaded["quality_report_hash"]), 64)
        self.assertEqual(downloaded["artifact_ref"], f"data://market.ohlcv.crypto.custom.1h/releases/{downloaded['release_id']}")
        self.assertEqual(bars["release_id"], downloaded["release_id"])
        self.assertEqual(bars["manifest_hash"], downloaded["manifest_hash"])
        self.assertEqual(added["status"], "draft")

    def test_registered_python_provider_download_publishes_materialized_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external = root / "provider-spec"
            external.mkdir()
            provider = external / "provider.py"
            contract = external / "sentiment.contract.json"
            spec = external / "sentiment.download.json"
            provider.write_text(
                "from pathlib import Path\n"
                "def acquire(product, scope, context):\n"
                "    count_path = Path(__file__).with_name('calls.txt')\n"
                "    count = int(count_path.read_text()) if count_path.exists() else 0\n"
                "    count_path.write_text(str(count + 1))\n"
                "    token = context['credentials']['KAIROS_TEST_PROVIDER_TOKEN']\n"
                "    sentiment = 0.8 if token == 'secret-token' else -1\n"
                "    return [{'available_time': scope['start'], 'instrument_id': product['instrument_id'], 'sentiment': sentiment}]\n",
                encoding="utf-8",
            )
            contract.write_text(json.dumps({
                "dataset_id": "reference.sentiment.provider",
                "primary_time": "available_time",
                "fields": ["available_time", "instrument_id", "sentiment"],
            }), encoding="utf-8")
            spec.write_text(json.dumps({
                "kind": "data.download",
                "key": "provider-sentiment",
                "scope": {"start": "2026-01-01T00:00:00Z"},
                "mode": {"acquire_missing": True},
                "source": {
                    "kind": "python_provider",
                    "path": "provider.py",
                    "credentials": {"env": ["KAIROS_TEST_PROVIDER_TOKEN"]},
                },
                "products": [{
                    "dataset_id": "reference.sentiment.provider",
                    "instrument_id": "equity:US:AAPL",
                    "contract": "sentiment.contract.json",
                }],
            }), encoding="utf-8")

            data = DataProductApi(root)
            registered = data.register_download("provider-sentiment", spec)
            original_token = os.environ.pop("KAIROS_TEST_PROVIDER_TOKEN", None)
            try:
                with self.assertRaisesRegex(ValueError, "missing required provider credentials"):
                    data.download("provider-sentiment")
                os.environ["KAIROS_TEST_PROVIDER_TOKEN"] = "secret-token"
                downloaded = data.download("provider-sentiment")
                rows = data.dataset("reference.sentiment.provider").rows(columns=("instrument_id", "sentiment"))
                os.environ.pop("KAIROS_TEST_PROVIDER_TOKEN", None)
                reused = data.download("provider-sentiment")
                study = StudyProductApi(root)
                study.open("provider-study")
                bound = study.add_data("provider-study", name="sentiment", dataset="reference.sentiment.provider")
                provider_calls = (external / "calls.txt").read_text(encoding="utf-8")
            finally:
                if original_token is None:
                    os.environ.pop("KAIROS_TEST_PROVIDER_TOKEN", None)
                else:
                    os.environ["KAIROS_TEST_PROVIDER_TOKEN"] = original_token

        self.assertEqual(registered["operation"], "register-download")
        self.assertEqual(downloaded["source"]["kind"], "python_provider")
        self.assertEqual(len(downloaded["source"]["provider_code_hash"]), 64)
        self.assertEqual(downloaded["source"]["credentials"]["required_env"], ["KAIROS_TEST_PROVIDER_TOKEN"])
        self.assertEqual(downloaded["source"]["row_count"], 1)
        self.assertEqual(rows[0]["instrument_id"], "equity:US:AAPL")
        self.assertEqual(rows[0]["sentiment"], 0.8)
        self.assertEqual(reused["release_id"], downloaded["release_id"])
        self.assertEqual(reused["source"]["acquire_policy"], "reused_existing_release")
        self.assertEqual(provider_calls, "1")
        self.assertEqual(bound["release_id"], downloaded["release_id"])
        self.assertNotIn("provider.py", json.dumps(bound))
        self.assertNotIn("secret-token", json.dumps(downloaded))

    def test_registered_provider_catalog_can_be_reused_by_download_spec(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provider_dir = root / "providers"
            spec_dir = root / "downloads"
            provider_dir.mkdir()
            spec_dir.mkdir()
            provider = provider_dir / "provider.py"
            provider_spec = provider_dir / "provider.json"
            contract = spec_dir / "provider.contract.json"
            download_spec = spec_dir / "provider.download.json"
            provider.write_text(
                "def acquire(product, scope, context):\n"
                "    return [{'available_time': scope['as_of'], 'ticker': product['ticker'], 'score': 7}]\n",
                encoding="utf-8",
            )
            provider_spec.write_text(json.dumps({
                "kind": "data.provider",
                "source": {"kind": "python_provider", "path": "provider.py"},
            }), encoding="utf-8")
            contract.write_text(json.dumps({
                "dataset_id": "reference.provider.catalog",
                "primary_time": "available_time",
                "fields": ["available_time", "ticker", "score"],
            }), encoding="utf-8")
            download_spec.write_text(json.dumps({
                "kind": "data.download",
                "scope": {"as_of": "2026-01-03T00:00:00Z"},
                "source": {"provider": "catalog-provider"},
                "products": [{
                    "dataset_id": "reference.provider.catalog",
                    "ticker": "AAPL",
                    "contract": "provider.contract.json",
                }],
            }), encoding="utf-8")

            data = DataProductApi(root)
            registered_provider = data.register_provider("catalog-provider", provider_spec)
            data.register_download("catalog-provider-data", download_spec)
            downloaded = data.download("catalog-provider-data")
            rows = data.dataset("reference.provider.catalog").rows(columns=("ticker", "score"))

        self.assertEqual(registered_provider["operation"], "register-provider")
        self.assertEqual(downloaded["source"]["provider"], "catalog-provider")
        self.assertEqual(downloaded["source"]["kind"], "python_provider")
        self.assertEqual(rows[0]["ticker"], "AAPL")
        self.assertEqual(rows[0]["score"], 7)

    def test_factor_metadata_contract_flows_from_study_to_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            factor_file = root / "momentum_factor.py"
            metadata_file = root / "momentum_factor.metadata.json"
            factor_file.write_text("def compute(inputs, params, context):\n    return inputs['bars']\n", encoding="utf-8")
            metadata_file.write_text(json.dumps({
                "inputs": {"bars": "bars"},
                "parameters": {"lookback_sessions": 252, "skip_recent_sessions": 21},
                "primary_time": "decision_time",
                "output_schema": {
                    "grain": "instrument_time",
                    "fields": ["instrument_id", "decision_time", "momentum_12_1"],
                },
                "point_in_time": True,
                "strategy_eligible": True,
                "dependencies": ["pandas"],
            }), encoding="utf-8")

            data = DataProductApi(root)
            study = StudyProductApi(root)
            strategy = StrategyProductApi(root)
            data.download("tutorial-sma-data")
            study.open("factor-metadata-study")
            study.add_data(
                "factor-metadata-study",
                name="bars",
                dataset="market.ohlcv.crypto.tutorial.btc-usdt.1h",
            )
            added_factor = study.add_factor(
                "factor-metadata-study",
                name="momentum_12_1",
                file=factor_file,
                metadata=metadata_file,
            )
            study_lock = study.freeze("factor-metadata-study", version="1.0.0")
            strategy.open("factor-metadata-strategy", from_study="factor-metadata-study@1.0.0")
            bound = strategy.bind_factor(
                "factor-metadata-strategy",
                name="primary",
                study_factor="momentum_12_1",
            )
            strategy_lock = strategy.freeze("factor-metadata-strategy", version="1.0.0")
            started = RunProductApi(root).start_snapshot("factor-metadata-strategy@1.0.0", mode="backtest")

        frozen_factor = study_lock["factors"]["momentum_12_1"]
        self.assertEqual(added_factor["metadata_status"], "declared")
        self.assertEqual(frozen_factor["inputs"], ["bars"])
        self.assertTrue(frozen_factor["point_in_time"])
        self.assertEqual(frozen_factor["primary_time"], "decision_time")
        self.assertEqual(frozen_factor["parameters"]["lookback_sessions"], 252)
        self.assertEqual(frozen_factor["output_schema"]["fields"], ["instrument_id", "decision_time", "momentum_12_1"])
        self.assertEqual(len(frozen_factor["factor_contract_hash"]), 64)
        self.assertEqual(len(frozen_factor["parameters_hash"]), 64)
        self.assertEqual(bound["factor_contract_hash"], frozen_factor["factor_contract_hash"])
        self.assertEqual(bound["parameters_hash"], frozen_factor["parameters_hash"])
        self.assertEqual(bound["output_schema"], frozen_factor["output_schema"])
        self.assertTrue(bound["point_in_time"])
        self.assertEqual(strategy_lock["inputs"]["primary"]["factor_contract_hash"], frozen_factor["factor_contract_hash"])
        self.assertEqual(
            started["input_artifacts"]["inputs"]["primary"]["factor_contract_hash"],
            frozen_factor["factor_contract_hash"],
        )
        self.assertEqual(
            started["input_artifacts"]["inputs"]["primary"]["parameters_hash"],
            frozen_factor["parameters_hash"],
        )

    def test_study_factor_run_writes_profile_and_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            factor_file = root / "factor.py"
            metadata_file = root / "factor.metadata.json"
            factor_file.write_text(
                "def compute(inputs, params, context):\n"
                "    rows = inputs['bars'].rows(columns=('available_time', 'close'))\n"
                "    return [{'available_time': row['available_time'], 'signal': row['close']} for row in rows[:2]]\n",
                encoding="utf-8",
            )
            metadata_file.write_text(json.dumps({
                "inputs": ["bars"],
                "parameters": {},
                "primary_time": "available_time",
                "fields": ["available_time", "signal"],
                "point_in_time": True,
            }), encoding="utf-8")

            data = DataProductApi(root)
            study = StudyProductApi(root)
            data.download("tutorial-sma-data")
            study.open("factor-run-study")
            study.add_data("factor-run-study", name="bars", dataset="market.ohlcv.crypto.tutorial.btc-usdt.1h")
            added = study.add_factor("factor-run-study", name="signal", file=factor_file, metadata=metadata_file)
            result = study.run_factor("factor-run-study", "signal")
            published = study.publish_factor(
                "factor-run-study",
                "signal",
                as_dataset="features.signal.crypto.tutorial",
            )
            feature_ref = data.dataset("features.signal.crypto.tutorial")
            profile = json.loads(Path(result["profile"]).read_text(encoding="utf-8"))
            rows = json.loads(Path(result["rows"]).read_text(encoding="utf-8"))

        self.assertEqual(result["operation"], "factor-run")
        self.assertEqual(result["row_count"], 2)
        self.assertEqual(profile["factor_contract_hash"], added["factor_contract_hash"])
        self.assertEqual(profile["point_in_time_check"], "declared")
        self.assertTrue(profile["passed"])
        self.assertEqual(rows[0]["signal"], "100")
        self.assertEqual(published["dataset_id"], "features.signal.crypto.tutorial")
        self.assertEqual(published["factor_run_hash"], result["run_hash"])
        self.assertEqual(feature_ref["release_id"], published["release_id"])
        self.assertEqual(len(published["manifest_hash"]), 64)
        self.assertEqual(len(published["quality_report_hash"]), 64)

    def test_study_factor_run_requires_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            factor_file = root / "factor.py"
            factor_file.write_text("def compute(inputs, params, context):\n    return []\n", encoding="utf-8")
            data = DataProductApi(root)
            study = StudyProductApi(root)
            data.download("tutorial-sma-data")
            study.open("factor-run-metadata-study")
            study.add_data("factor-run-metadata-study", name="bars", dataset="market.ohlcv.crypto.tutorial.btc-usdt.1h")
            study.add_factor("factor-run-metadata-study", name="legacy", file=factor_file)

            with self.assertRaisesRegex(ValueError, "requires metadata"):
                study.run_factor("factor-run-metadata-study", "legacy")

    def test_factor_metadata_rejects_undeclared_study_input_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            factor_file = root / "factor.py"
            metadata_file = root / "factor.metadata.json"
            factor_file.write_text("def compute(inputs, params, context):\n    return inputs['returns']\n", encoding="utf-8")
            metadata_file.write_text(json.dumps({
                "inputs": ["returns"],
                "parameters": {},
                "primary_time": "decision_time",
                "fields": ["instrument_id", "decision_time", "signal"],
                "point_in_time": True,
            }), encoding="utf-8")

            study = StudyProductApi(root)
            study.open("bad-factor-study")
            with self.assertRaisesRegex(ValueError, "undeclared study data aliases: returns"):
                study.add_factor("bad-factor-study", name="bad_factor", file=factor_file, metadata=metadata_file)

    def test_strategy_rejects_factor_metadata_marked_not_strategy_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            factor_file = root / "exploration_factor.py"
            metadata_file = root / "exploration_factor.metadata.json"
            factor_file.write_text("def compute(inputs, params, context):\n    return inputs['bars']\n", encoding="utf-8")
            metadata_file.write_text(json.dumps({
                "inputs": ["bars"],
                "parameters": {"window": 5},
                "primary_time": "available_time",
                "fields": ["instrument_id", "available_time", "temporary_signal"],
                "point_in_time": True,
                "strategy_eligible": False,
            }), encoding="utf-8")

            data = DataProductApi(root)
            study = StudyProductApi(root)
            strategy = StrategyProductApi(root)
            data.download("tutorial-sma-data")
            study.open("exploration-factor-study")
            study.add_data(
                "exploration-factor-study",
                name="bars",
                dataset="market.ohlcv.crypto.tutorial.btc-usdt.1h",
            )
            study.add_factor(
                "exploration-factor-study",
                name="temporary_signal",
                file=factor_file,
                metadata=metadata_file,
            )
            study.freeze("exploration-factor-study", version="1.0.0")
            strategy.open("exploration-factor-strategy", from_study="exploration-factor-study@1.0.0")

            with self.assertRaisesRegex(ValueError, "not strategy eligible"):
                strategy.bind_factor(
                    "exploration-factor-strategy",
                    name="primary",
                    study_factor="temporary_signal",
                )

    def test_strategy_freeze_checks_factor_contract_hash_consistency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            factor_file = root / "factor.py"
            metadata_file = root / "factor.metadata.json"
            factor_file.write_text("def compute(inputs, params, context):\n    return inputs['bars']\n", encoding="utf-8")
            metadata_file.write_text(json.dumps({
                "inputs": ["bars"],
                "parameters": {"window": 12},
                "primary_time": "available_time",
                "fields": ["instrument_id", "available_time", "signal"],
                "point_in_time": True,
            }), encoding="utf-8")

            data = DataProductApi(root)
            study = StudyProductApi(root)
            strategy = StrategyProductApi(root)
            data.download("tutorial-sma-data")
            study.open("contract-hash-study")
            study.add_data(
                "contract-hash-study",
                name="bars",
                dataset="market.ohlcv.crypto.tutorial.btc-usdt.1h",
            )
            study.add_factor("contract-hash-study", name="signal", file=factor_file, metadata=metadata_file)
            study.freeze("contract-hash-study", version="1.0.0")
            strategy.open("contract-hash-strategy", from_study="contract-hash-study@1.0.0")
            strategy.bind_factor("contract-hash-strategy", name="primary", study_factor="signal")
            strategy_file = root / "strategies" / "contract-hash-strategy" / "strategy.json"
            payload = json.loads(strategy_file.read_text(encoding="utf-8"))
            payload["inputs"]["primary"]["factor_contract_hash"] = "0" * 64
            strategy_file.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "factor_contract_hash does not match"):
                strategy.freeze("contract-hash-strategy", version="1.0.0")

    def test_strategy_execution_policy_requires_execution_contract_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = DataProductApi(root)
            study = StudyProductApi(root)
            strategy = StrategyProductApi(root)
            bad_execution = root / "bad-execution.json"
            good_execution = root / "execution.json"
            bad_execution.write_text(json.dumps({"decision_time": "session_close"}), encoding="utf-8")
            good_execution.write_text(json.dumps({
                "kind": "strategy.execution",
                "execution": {
                    "decision_time": "session_close",
                    "execution_time": "next_session_open",
                    "order_style": "market_on_open_proxy",
                },
            }), encoding="utf-8")

            data.download("tutorial-sma-data")
            study.open("execution-policy-study")
            study.add_data(
                "execution-policy-study",
                name="bars",
                dataset="market.ohlcv.crypto.tutorial.btc-usdt.1h",
            )
            study.freeze("execution-policy-study", version="1.0.0")
            strategy.open("execution-policy-strategy", from_study="execution-policy-study@1.0.0")
            with self.assertRaisesRegex(ValueError, "execution policy must declare"):
                strategy.set_execution("execution-policy-strategy", bad_execution)
            execution = strategy.set_execution("execution-policy-strategy", good_execution)

        self.assertEqual(execution["operation"], "set-execution")
        self.assertEqual(len(execution["execution_policy_hash"]), 64)

    def test_strategy_model_code_contract_enters_strategy_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            factor_file = root / "factor.py"
            metadata_file = root / "factor.metadata.json"
            model_file = root / "model.py"
            model_metadata = root / "model.metadata.json"
            factor_file.write_text("def compute(inputs, params, context):\n    return inputs['bars']\n", encoding="utf-8")
            metadata_file.write_text(json.dumps({
                "inputs": ["bars"],
                "parameters": {},
                "primary_time": "available_time",
                "fields": ["instrument_id", "available_time", "signal"],
                "point_in_time": True,
            }), encoding="utf-8")
            model_file.write_text(
                "def decide(context):\n"
                "    rows = context.input('bars').rows(columns=('available_time', 'close'))\n"
                "    return {'intent': 'hold', 'observed_close': rows[0]['close']}\n",
                encoding="utf-8",
            )
            model_metadata.write_text(json.dumps({
                "inputs": ["primary", "bars"],
                "intent_schema": {"kind": "target_exposure", "fields": ["instrument_id", "target_weight"]},
                "side_effects_allowed": False,
            }), encoding="utf-8")

            data = DataProductApi(root)
            study = StudyProductApi(root)
            strategy = StrategyProductApi(root)
            data.download("tutorial-sma-data")
            study.open("model-code-study")
            study.add_data("model-code-study", name="bars", dataset="market.ohlcv.crypto.tutorial.btc-usdt.1h")
            study.add_factor("model-code-study", name="signal", file=factor_file, metadata=metadata_file)
            study.freeze("model-code-study", version="1.0.0")
            strategy.open("model-code-strategy", from_study="model-code-study@1.0.0")
            strategy.bind_factor("model-code-strategy", name="primary", study_factor="signal")
            model = strategy.set_model_code("model-code-strategy", model_file, metadata=model_metadata)
            model_file.write_text("def decide(context):\n    raise RuntimeError('draft file should not run')\n", encoding="utf-8")
            lock = strategy.freeze("model-code-strategy", version="1.0.0")
            started = RunProductApi(root).start("model-code-strategy@1.0.0", mode="backtest", execute_strategy=True)
            decision = json.loads(Path(started["outputs"]["strategy_decision"]).read_text(encoding="utf-8"))
            model_artifact_exists = Path(model["artifact_path"]).exists()

        self.assertEqual(model["metadata_status"], "declared")
        self.assertEqual(len(model["model_code_hash"]), 64)
        self.assertEqual(len(model["model_contract_hash"]), 64)
        self.assertTrue(model_artifact_exists)
        self.assertEqual(lock["model"]["model_contract_hash"], model["model_contract_hash"])
        self.assertEqual(lock["consistency_checks"]["model_contract_hash"], "passed")
        self.assertEqual(started["target"]["hash"], lock["lock_hash"])
        self.assertEqual(started["runtime_contract"]["strategy_decision_execution"]["decision_hash"], decision["decision_hash"])
        self.assertEqual(decision["decision"]["observed_close"], "100")

    def test_published_factor_becomes_strategy_input_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            factor_file = root / "factor.py"
            metadata_file = root / "factor.metadata.json"
            model_file = root / "model.py"
            model_metadata = root / "model.metadata.json"
            factor_file.write_text(
                "def compute(inputs, params, context):\n"
                "    rows = inputs['bars'].rows(columns=('available_time', 'close'))\n"
                "    return [{'available_time': rows[0]['available_time'], 'signal': rows[0]['close']}]\n",
                encoding="utf-8",
            )
            metadata_file.write_text(json.dumps({
                "inputs": ["bars"],
                "parameters": {},
                "primary_time": "available_time",
                "fields": ["available_time", "signal"],
                "point_in_time": True,
            }), encoding="utf-8")
            model_file.write_text(
                "def decide(context):\n"
                "    rows = context.input('primary').rows(columns=('available_time', 'signal'))\n"
                "    return {'intent': 'rank', 'signal': rows[0]['signal']}\n",
                encoding="utf-8",
            )
            model_metadata.write_text(json.dumps({
                "inputs": ["primary"],
                "intent_schema": {"kind": "ranked_signal", "fields": ["available_time", "signal"]},
                "side_effects_allowed": False,
            }), encoding="utf-8")

            data = DataProductApi(root)
            study = StudyProductApi(root)
            strategy = StrategyProductApi(root)
            data.download("tutorial-sma-data")
            study.open("published-factor-study")
            study.add_data("published-factor-study", name="bars", dataset="market.ohlcv.crypto.tutorial.btc-usdt.1h")
            study.add_factor("published-factor-study", name="signal", file=factor_file, metadata=metadata_file)
            factor_run = study.run_factor("published-factor-study", "signal")
            published = study.publish_factor(
                "published-factor-study",
                "signal",
                as_dataset="features.signal.strategy.input",
            )
            study_lock = study.freeze("published-factor-study", version="1.0.0")
            strategy.open("published-factor-strategy", from_study="published-factor-study@1.0.0")
            bound = strategy.bind_factor("published-factor-strategy", name="primary", study_factor="signal")
            strategy.set_model_code("published-factor-strategy", model_file, metadata=model_metadata)
            strategy.freeze("published-factor-strategy", version="1.0.0")
            started = RunProductApi(root).start("published-factor-strategy@1.0.0", mode="backtest", execute_strategy=True)
            decision = json.loads(Path(started["outputs"]["strategy_decision"]).read_text(encoding="utf-8"))

        self.assertEqual(study_lock["published_factors"]["signal"]["release_id"], published["release_id"])
        self.assertEqual(bound["materialization_status"], "published_feature")
        self.assertEqual(bound["release_id"], published["release_id"])
        self.assertEqual(bound["factor_run_hash"], factor_run["run_hash"])
        self.assertEqual(started["input_artifacts"]["inputs"]["primary"]["release_id"], published["release_id"])
        self.assertEqual(decision["decision"]["signal"], 100)

    def test_strategy_model_metadata_rejects_undeclared_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_file = root / "model.py"
            model_metadata = root / "model.metadata.json"
            model_file.write_text("def decide(context):\n    return None\n", encoding="utf-8")
            model_metadata.write_text(json.dumps({
                "inputs": ["missing_signal"],
                "intent_schema": {"kind": "target_exposure"},
                "side_effects_allowed": False,
            }), encoding="utf-8")
            data = DataProductApi(root)
            study = StudyProductApi(root)
            strategy = StrategyProductApi(root)
            data.download("tutorial-sma-data")
            study.open("bad-model-study")
            study.add_data("bad-model-study", name="bars", dataset="market.ohlcv.crypto.tutorial.btc-usdt.1h")
            study.freeze("bad-model-study", version="1.0.0")
            strategy.open("bad-model-strategy", from_study="bad-model-study@1.0.0")

            with self.assertRaisesRegex(ValueError, "undeclared inputs: missing_signal"):
                strategy.set_model_code("bad-model-strategy", model_file, metadata=model_metadata)

    def test_cli_registered_download_resolves_paths_relative_to_spec(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external = root / "specs"
            external.mkdir()
            contract = external / "sentiment.contract.json"
            csv_file = external / "sentiment.csv"
            spec = external / "sentiment.download.json"
            contract.write_text(json.dumps({
                "dataset_id": "reference.sentiment.cli",
                "primary_time": "available_time",
                "fields": ["available_time", "instrument_id", "sentiment"],
            }), encoding="utf-8")
            csv_file.write_text(
                "available_time,instrument_id,sentiment\n"
                "2026-01-01T00:00:00Z,equity:US:MSFT,0.7\n",
                encoding="utf-8",
            )
            spec.write_text(json.dumps({
                "kind": "data.download",
                "source": {"kind": "local_csv", "path": "sentiment.csv"},
                "products": [{
                    "dataset_id": "reference.sentiment.cli",
                    "contract": "sentiment.contract.json",
                }],
            }), encoding="utf-8")

            registered = command(root, "data", "register-download", "--key", "cli-sentiment", "--spec", str(spec))
            downloaded = command(root, "data", "download", "cli-sentiment")
            report_exists = Path(downloaded["report"]).exists()
            quality_report_exists = Path(downloaded["quality_report"]).exists()

        self.assertEqual(registered["key"], "cli-sentiment")
        self.assertEqual(downloaded["key"], "cli-sentiment")
        self.assertEqual(downloaded["dataset_id"], "reference.sentiment.cli")
        self.assertEqual(len(downloaded["releases"]), 1)
        self.assertTrue(report_exists)
        self.assertTrue(quality_report_exists)
        self.assertEqual(len(downloaded["quality_report_hash"]), 64)

    def test_data_study_strategy_run_user_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external = root / "external-input"
            external.mkdir()
            contract = external / "sentiment.contract.json"
            csv_file = external / "sentiment.csv"
            live_connector = external / "sentiment_live.py"
            factor_file = external / "momentum_factor.py"
            risk_file = external / "risk.json"
            execution_file = external / "execution.json"

            contract.write_text(json.dumps({
                "dataset_id": "reference.sentiment.equity.us",
                "primary_time": "available_time",
                "grain": {"kind": "event_stream"},
                "fields": ["available_time", "instrument_id", "sentiment"],
                "freshness": {"max_age_seconds": 60},
            }), encoding="utf-8")
            csv_file.write_text(
                "available_time,instrument_id,sentiment\n"
                "2026-01-01T00:00:00Z,equity:US:AAPL,0.4\n",
                encoding="utf-8",
            )
            live_connector.write_text(
                "def subscribe(params, context):\n"
                "    yield {'available_time': '2026-01-01T00:00:00Z', 'instrument_id': 'equity:US:AAPL', 'sentiment': 0.4}\n",
                encoding="utf-8",
            )
            factor_file.write_text(
                "def compute(data):\n"
                "    return data['bars']\n",
                encoding="utf-8",
            )
            risk_file.write_text(json.dumps({"max_gross_exposure": 1.0}), encoding="utf-8")
            execution_file.write_text(json.dumps({
                "decision_time": "session_close",
                "execution_time": "next_session_open",
                "order_style": "market_on_open_proxy",
            }), encoding="utf-8")

            downloaded = command(root, "data", "download", "tutorial-sma-data")
            written = command(
                root,
                "data",
                "write",
                "--file",
                str(csv_file),
                "--as",
                "reference.sentiment.equity.us",
                "--contract",
                str(contract),
            )
            live_view = command(
                root,
                "data",
                "write",
                "--live",
                "--connector",
                str(live_connector),
                "--as",
                "reference.sentiment.equity.us",
                "--contract",
                str(contract),
            )
            study = command(root, "study", "open", "momentum-study", "--hypothesis", "momentum persists")
            bars = command(
                root,
                "study",
                "add-data",
                "--workspace",
                "momentum-study",
                "--name",
                "bars",
                "--dataset",
                "market.ohlcv.crypto.tutorial.btc-usdt.1h",
            )
            sentiment = command(
                root,
                "study",
                "add-data",
                "--workspace",
                "momentum-study",
                "--name",
                "sentiment",
                "--dataset",
                "reference.sentiment.equity.us",
            )
            factor = command(
                root,
                "study",
                "add-factor",
                "--workspace",
                "momentum-study",
                "--name",
                "momentum_12_1",
                "--file",
                str(factor_file),
            )
            study_run = command(root, "run", "start", "--study", "momentum-study", "--mode", "study")
            study_lock = command(root, "study", "freeze", "momentum-study", "--version", "1.0.0")
            strategy = command(
                root,
                "strategy",
                "open",
                "momentum-long-only",
                "--from-study",
                "momentum-study@1.0.0",
            )
            bound_factor = command(
                root,
                "strategy",
                "bind-factor",
                "--workspace",
                "momentum-long-only",
                "--name",
                "primary",
                "--study-factor",
                "momentum_12_1",
            )
            risk = command(root, "strategy", "set-risk", "momentum-long-only", str(risk_file))
            execution = command(root, "strategy", "set-execution", "momentum-long-only", str(execution_file))
            strategy_lock = command(root, "strategy", "freeze", "momentum-long-only", "--version", "1.0.0")
            backtest_run = command(root, "run", "start", "--snapshot", "momentum-long-only@1.0.0", "--mode", "backtest")
            inspected = command(root, "run", "inspect", "--run-id", backtest_run["run_id"])
            replayed = command(root, "run", "replay", "--run-id", backtest_run["run_id"])
            compared = command(
                root,
                "run",
                "compare",
                "--first",
                study_run["run_id"],
                "--second",
                backtest_run["run_id"],
            )
            backtest_manifest_exists = Path(backtest_run["manifest"]).exists()

        self.assertEqual(downloaded["product"], "data")
        self.assertEqual(downloaded["release_id"], "fixture:sma-bars-v1")
        self.assertEqual(written["dataset_id"], "reference.sentiment.equity.us")
        self.assertEqual(written["primary_time"], "available_time")
        self.assertEqual(len(written["contract_hash"]), 64)
        self.assertEqual(len(written["manifest_hash"]), 64)
        self.assertEqual(len(written["quality_report_hash"]), 64)
        self.assertEqual(written["artifact_ref"], f"data://reference.sentiment.equity.us/releases/{written['release_id']}")
        self.assertEqual(live_view["kind"], "live_view_manifest")
        self.assertEqual(live_view["contract_hash"], written["contract_hash"])
        self.assertEqual(len(live_view["manifest_hash"]), 64)
        self.assertEqual(live_view["live_data_plane"]["channel_contract"], "BoundedEventChannel")
        self.assertEqual(live_view["live_data_plane"]["freshness"]["max_age_seconds"], 60)
        self.assertEqual(study["product"], "study")
        self.assertEqual(study_run["mode"], "study")
        self.assertEqual(study_run["runtime_contract"]["mode"], "study")
        self.assertEqual(study_run["runtime_contract"]["run_mode_composition"]["execution_driver"], "none")
        self.assertEqual(len(study_run["runtime_contract"]["run_mode_composition"]["composition_hash"]), 64)
        self.assertEqual(bars["release_id"], "fixture:sma-bars-v1")
        self.assertEqual(sentiment["release_id"], written["release_id"])
        self.assertEqual(sentiment["contract_hash"], written["contract_hash"])
        self.assertEqual(sentiment["manifest_hash"], written["manifest_hash"])
        self.assertEqual(sentiment["artifact_ref"], written["artifact_ref"])
        self.assertEqual(len(factor["code_hash"]), 64)
        self.assertEqual(study_lock["data"]["bars"]["release_id"], "fixture:sma-bars-v1")
        self.assertEqual(study_lock["data"]["sentiment"]["contract_hash"], written["contract_hash"])
        self.assertEqual(study_lock["evidence_chain"]["data"]["sentiment"]["manifest_hash"], written["manifest_hash"])
        self.assertEqual(study_lock["factors"]["momentum_12_1"]["code_hash"], factor["code_hash"])
        self.assertEqual(strategy["derived_from"]["lock_hash"], study_lock["lock_hash"])
        self.assertEqual(strategy["data"]["sentiment"]["content_hash"], study_lock["data"]["sentiment"]["content_hash"])
        self.assertEqual(bound_factor["source_hash"], factor["code_hash"])
        self.assertEqual(len(risk["risk_hash"]), 64)
        self.assertEqual(len(risk["risk_policy_hash"]), 64)
        self.assertEqual(len(execution["execution_policy_hash"]), 64)
        self.assertEqual(strategy_lock["data"]["sentiment"], study_lock["data"]["sentiment"])
        self.assertEqual(strategy_lock["risk"]["risk_policy_hash"], risk["risk_policy_hash"])
        self.assertEqual(strategy_lock["execution"]["execution_policy_hash"], execution["execution_policy_hash"])
        self.assertEqual(strategy_lock["consistency_checks"]["risk_policy_hash"], "passed")
        self.assertEqual(strategy_lock["consistency_checks"]["execution_policy_hash"], "passed")
        self.assertEqual(strategy_lock["consistency_checks"]["data_release_hashes"], "passed")
        self.assertEqual(strategy_lock["inputs"]["primary"]["source_hash"], factor["code_hash"])
        self.assertEqual(backtest_run["target"]["hash"], strategy_lock["lock_hash"])
        self.assertEqual(backtest_run["runtime_contract"]["run_mode_composition"]["clock"], "replay")
        self.assertEqual(backtest_run["runtime_contract"]["run_mode_composition"]["execution_driver"], "fill-model")
        self.assertEqual(backtest_run["input_artifacts"]["data"]["sentiment"]["manifest_hash"], written["manifest_hash"])
        self.assertEqual(backtest_run["input_artifacts"]["inputs"]["primary"]["source_hash"], factor["code_hash"])
        self.assertEqual(inspected["run_id"], backtest_run["run_id"])
        self.assertEqual(inspected["input_artifacts"], backtest_run["input_artifacts"])
        self.assertTrue(replayed["passed"])
        self.assertFalse(compared["same_target"])
        self.assertFalse(compared["same_mode"])
        self.assertTrue(backtest_manifest_exists)

    def test_study_run_mode_writes_study_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command(root, "init")
            command(root, "study", "start", "study-mode-study")
            started = command(root, "run", "start", "--study", "study-mode-study", "--mode", "study")

        self.assertEqual(started["mode"], "study")
        self.assertEqual(started["runtime_contract"]["mode"], "study")
        self.assertEqual(started["runtime_contract"]["run_mode_composition"]["mode"], "study")
