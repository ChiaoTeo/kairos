from __future__ import annotations

import asyncio
import json
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
            started = run.start_snapshot("api-strategy@1.0.0", mode="backtest")
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
        self.assertTrue(replayed["passed"])

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
        self.assertEqual(strategy_lock["data"]["sentiment"], study_lock["data"]["sentiment"])
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
