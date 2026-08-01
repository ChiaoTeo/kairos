from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping

import pytest
from typer.testing import CliRunner

from kairospy.application.service.modes.backtest import BacktestConfigurationError, configured_backtest
from kairospy.application.launch import TradingConfigurationError, TradingSystemLauncher
from kairospy.application.domain.reference import catalog_from_market_rows
from kairospy.core.market import Bar
from kairospy.infrastructure.persistence.market_data.catalog import DataStore
from kairospy.infrastructure.persistence.reference.sqlite_store import SqliteReferenceStore
from kairospy.surface.timeline.loader import TimelineDataLoader
from kairospy.surface.cli.commands.launch import launch_app


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)


def test_configured_backtest_launches_new_engine_and_writes_account_current(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)

    configured = configured_backtest(config_path)
    result = TradingSystemLauncher().launch_configured_backtest(configured)

    assert result.runtime.event_count == 2
    assert len(result.fills) == 1
    assert len(result.equity_curve) == 2
    assert result.metrics.net_profit == result.net_profit
    assert result.final_equity == Decimal("1000")
    current = json.loads((configured.launch_directory / "account" / "current.json").read_text(encoding="utf-8"))
    assert current["launch_id"] == "bt-1"
    assert current["equity"] == str(result.account_view.equity)


def test_configured_backtest_applies_account_fee_rate(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path, fee_rate="0.001")

    configured = configured_backtest(config_path)
    result = TradingSystemLauncher().launch_configured_backtest(configured)

    assert configured.normalized_config["account"]["fee_rate"] == Decimal("0.001")
    assert result.fills[0].fee == Decimal("0.202")
    assert result.account_view.cash == Decimal("797.798")


def test_backtest_downloads_missing_history_after_strategy_subscribe(tmp_path, monkeypatch) -> None:
    config_path = _write_backtest_project(tmp_path, seed_data=False, on_missing="download")

    monkeypatch.setattr("kairospy.application.launch.launcher.exchange", lambda exchange_name, driver_name: FakeHistoricalClient())

    result = TradingSystemLauncher().launch_backtest_config(config_path)

    assert len(result.fills) == 1
    rows = DataStore(tmp_path / ".kairos" / "data", storage_format="jsonl").read_rows("market.ohlcv.binance.spot.btc_usdt.1m")
    assert rows[0]["close"] == "101"


def test_backtest_strategy_subscribes_by_dataset_id(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path, subscribe_by_dataset=True)

    result = TradingSystemLauncher().launch_backtest_config(config_path)

    assert result.runtime.event_count == 2
    assert len(result.fills) == 1


def test_configured_backtest_mounts_workspace_reference_catalog(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    SqliteReferenceStore(tmp_path / ".kairos" / "reference").save_catalog(
        catalog_from_market_rows(
            (
                {
                    "venue": "binance",
                    "market": "spot",
                    "source_symbol": "BTC/USDT",
                    "base": "BTC",
                    "quote": "USDT",
                    "status": "trading",
                },
            ),
            effective_from=as_of,
        )
    )

    result = TradingSystemLauncher().launch_configured_backtest(configured_backtest(config_path))

    catalog = result.views.require("reference.catalog")
    markets = result.views.require("reference.markets")
    resolved = result.views.require("reference.market.binance_spot_btc_usdt")
    assert catalog.market_count == 1
    assert markets.markets[0].market_key == "binance_spot_btc_usdt"
    assert str(resolved.ref.market_id) == "market:binance:spot:btc_usdt"


def test_launch_backtest_command_writes_launch_artifacts(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)

    result = CliRunner().invoke(launch_app, ["start", str(config_path), "--format", "json"], catch_exceptions=False)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["launch_id"] == "bt-1"
    assert payload["launch_instance_id"]
    assert payload["result"]["event_count"] == 2
    launch_directory = Path(payload["directory"])
    group_directory = tmp_path / ".kairos" / "launches" / "backtest" / "bt-1"
    assert launch_directory == group_directory / "instances" / payload["launch_instance_id"]
    assert (launch_directory / "summary.json").exists()
    assert (launch_directory / "account" / "current.json").exists()
    log_text = (launch_directory / "launch.log").read_text(encoding="utf-8")
    assert "Launch Environment" in log_text
    assert "System Status" in log_text
    assert "Account Status" in log_text
    for legacy_name in (
        "equity.jsonl",
        "fills.jsonl",
        "trades.jsonl",
        "intent_states.jsonl",
        "decision_trace.jsonl",
        "risk_snapshots.jsonl",
    ):
        assert not (launch_directory / legacy_name).exists()
    timeline_rows = [json.loads(line) for line in (launch_directory / "timeline.jsonl").read_text(encoding="utf-8").splitlines()]
    assert timeline_rows
    assert {row["trigger"] for row in timeline_rows} >= {"interval", "intent_created", "intent_completed", "fill"}
    assert "context_hash" in timeline_rows[0]
    assert "system.strategy" in timeline_rows[0]["views"]
    assert "account.risk_snapshots" in timeline_rows[-1]["views"]
    assert "execution.fills" in timeline_rows[-1]["views"]
    timeline_data = TimelineDataLoader(launch_directory).load()
    assert len(timeline_data["records"]["equity"]) == 2
    assert len(timeline_data["records"]["fills"]) == 1
    assert len(timeline_data["records"]["intents"]) == 1
    assert timeline_data["records"]["fills"][0]["market_id"] == "market:binance:spot:btc_usdt"
    assert timeline_data["records"]["intents"][0]["intent_id"] == "intent-1"
    assert timeline_data["records"]["intents"][0]["status"] == "satisfied"
    assert json.loads((launch_directory / "metrics.json").read_text(encoding="utf-8"))["net_profit"] == "0"
    assert json.loads((launch_directory / "summary.json").read_text(encoding="utf-8"))["event_count"] == 2
    current = json.loads((group_directory / "current.json").read_text(encoding="utf-8"))
    assert current["launch_instance_id"] == payload["launch_instance_id"]


def test_decision_trace_artifact_requires_explicit_strategy_trace(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)

    result = CliRunner().invoke(launch_app, ["start", str(config_path), "--format", "json"], catch_exceptions=False)

    launch_directory = Path(json.loads(result.output)["directory"])
    assert not (launch_directory / "decision_trace.jsonl").exists()
    timeline_rows = [json.loads(line) for line in (launch_directory / "timeline.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {row["trigger"] for row in timeline_rows} >= {"intent_created", "intent_completed", "fill"}
    assert "decision" not in {row["trigger"] for row in timeline_rows}
    assert TimelineDataLoader(launch_directory).load()["records"]["decisionTrace"] == []


def test_explicit_strategy_trace_writes_decision_trace_and_timeline_decision(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path, emit_trace=True)

    result = CliRunner().invoke(launch_app, ["start", str(config_path), "--format", "json"], catch_exceptions=False)

    launch_directory = Path(json.loads(result.output)["directory"])
    decision_file_rows = [json.loads(line) for line in (launch_directory / "decision_trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(decision_file_rows) == 1
    assert decision_file_rows[0]["name"] == "entry_signal"
    decision_rows = TimelineDataLoader(launch_directory).load()["records"]["decisionTrace"]
    assert len(decision_rows) == 1
    assert decision_rows[0]["name"] == "entry_signal"
    assert decision_rows[0]["payload"]["action"] == "target_position"
    timeline_rows = [json.loads(line) for line in (launch_directory / "timeline.jsonl").read_text(encoding="utf-8").splitlines()]
    assert "decision" in {row["trigger"] for row in timeline_rows}


def test_launch_backtest_command_accepts_strategy_runtime_override(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace('strategy = "strategy_mod:ConfiguredStrategy"\n', ""),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        launch_app,
        ["start", str(config_path), "--strategy", "strategy_mod:ConfiguredStrategy", "--format", "json"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["result"]["strategy_id"] == "configured-strategy"
    assert payload["result"]["event_count"] == 2


def test_launch_backtest_command_creates_a_new_instance_each_start(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)

    first = CliRunner().invoke(launch_app, ["start", str(config_path), "--format", "json"], catch_exceptions=False)
    second = CliRunner().invoke(launch_app, ["start", str(config_path), "--format", "json"], catch_exceptions=False)

    first_payload = json.loads(first.output)
    second_payload = json.loads(second.output)
    assert first_payload["launch_instance_id"] != second_payload["launch_instance_id"]
    assert Path(first_payload["directory"]).exists()
    assert Path(second_payload["directory"]).exists()
    current = json.loads((tmp_path / ".kairos" / "launches" / "backtest" / "bt-1" / "current.json").read_text(encoding="utf-8"))
    assert current["launch_instance_id"] == second_payload["launch_instance_id"]


def test_launch_backtest_command_uses_new_config_runner(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)

    result = CliRunner().invoke(launch_app, ["start", str(config_path), "--format", "json"], catch_exceptions=False)

    assert result.exit_code == 0
    assert '"launch_id": "bt-1"' in result.output
    assert '"event_count": 2' in result.output


def test_launch_backtest_config_wraps_recipe_configuration_errors(tmp_path) -> None:
    config_path = tmp_path / "invalid.toml"
    config_path.write_text(
        "\n".join([
            "[launch]",
            'id = "bad"',
            'mode = "backtest"',
            'strategy = "missing:factory"',
        ])
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(TradingConfigurationError):
        TradingSystemLauncher().launch_backtest_config(config_path)


def test_configured_backtest_rejects_legacy_event_source(tmp_path) -> None:
    config_path = tmp_path / "legacy.toml"
    config_path.write_text(
        "\n".join([
            "[launch]",
            'id = "legacy"',
            'mode = "backtest"',
            'strategy = "strategy_mod:ConfiguredStrategy"',
            "",
            "[backtest]",
            'events = "events.jsonl"',
        ])
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(BacktestConfigurationError, match="backtest.events is no longer supported"):
        configured_backtest(config_path)


def test_launch_backtest_config_does_not_wrap_strategy_runtime_value_error(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)
    (tmp_path / "strategy_mod.py").write_text(
        "\n".join([
            "from kairospy.application.strategy import StrategyBase",
            "from kairospy.core.market import Bar",
            "from kairospy.core.reference import MarketRef",
            "",
            "class ConfiguredStrategy(StrategyBase):",
            "    strategy_id = 'configured-strategy'",
            "    def __init__(self, instrument_id, market_id):",
            "        self.market_ref = MarketRef(",
            "            market_id=market_id,",
            "            instrument_id=instrument_id,",
            "            market_key='binance_spot_btc_usdt',",
            "            venue='binance',",
            "            market='spot',",
            "            source_symbol='BTC/USDT',",
            "        )",
            "    def on_start(self, context):",
            "        context.subscribe(self.market_ref, selectors=(Bar.select(interval='1m'),), identity=self.strategy_id)",
            "    def on_data(self, context, signal):",
            "        raise ValueError('strategy runtime failed')",
        ])
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as error:
        TradingSystemLauncher().launch_backtest_config(config_path)
    assert not isinstance(error.value, TradingConfigurationError)
    assert str(error.value) == "strategy runtime failed"


class FakeHistoricalClient:
    def fetch_bars(
        self,
        symbol: str,
        *,
        timeframe: str = "1m",
        since: object | None = None,
        until: object | None = None,
        limit: int = 1000,
        adapter_options: Mapping[str, object] | None = None,
    ):
        _ = since, until, limit, adapter_options
        return (
            Bar(
                instrument_id="instrument:spot:btc:usdt",
                market_id="market:binance:spot:btc_usdt",
                market_key="binance_spot_btc_usdt",
                time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                timeframe=timeframe,
                open=Decimal("101"),
                high=Decimal("101"),
                low=Decimal("101"),
                close=Decimal("101"),
                volume=Decimal("1"),
                source=symbol,
            ),
        )


def _write_backtest_project(
    root: Path,
    *,
    seed_data: bool = True,
    on_missing: str = "error",
    fee_rate: str | None = None,
    subscribe_by_dataset: bool = False,
    emit_trace: bool = False,
) -> Path:
    (root / ".kairos").mkdir(parents=True, exist_ok=True)
    (root / ".kairos" / "kairos.toml").write_text("[project]\nname = \"test\"\n", encoding="utf-8")
    market_id = "market:binance:spot:btc_usdt"
    instrument_id = "instrument:spot:btc:usdt"
    (root / "strategy_mod.py").write_text(
        "\n".join([
            "from decimal import Decimal",
            "from kairospy.application.strategy import StrategyBase",
            "from kairospy.core.market import Bar",
            "from kairospy.core.intent import target_position_intent",
            "from kairospy.core.reference import MarketRef",
            "",
            "class ConfiguredStrategy(StrategyBase):",
            "    strategy_id = 'configured-strategy'",
            "    def __init__(self, instrument_id, market_id):",
            "        self.instrument_id = instrument_id",
            "        self.market_id = market_id",
            "        self.market_ref = MarketRef(",
            "            market_id=market_id,",
            "            instrument_id=instrument_id,",
            "            market_key='binance_spot_btc_usdt',",
            "            venue='binance',",
            "            market='spot',",
            "            source_symbol='BTC/USDT',",
            "        )",
            "        self.entered = False",
            "    def on_start(self, context):",
            (
                "        context.subscribe('market.ohlcv.binance.spot.btc_usdt.1m', identity=self.strategy_id)"
                if subscribe_by_dataset
                else "        context.subscribe(self.market_ref, selectors=(Bar.select(interval='1m'),), identity=self.strategy_id)"
            ),
            "    def on_data(self, context, signal):",
            "        if self.entered:",
            "            return None",
            "        self.entered = True",
            *(
                [
                    "        context.trace('entry_signal', {",
                    "            'action': 'target_position',",
                    "            'instrument_id': self.instrument_id,",
                    "            'target_quantity': '2',",
                    "            'reason': 'first_bar_entry',",
                    "        })",
                ]
                if emit_trace
                else []
            ),
            "        context.intent(target_position_intent(",
            "            strategy_id=self.strategy_id,",
            "            instrument_id=self.instrument_id,",
            "            market_id=self.market_id,",
            "            target_quantity=Decimal('2'),",
            "            at=signal.time,",
            "            intent_id='intent-1',",
            "        ))",
            "        return None",
        ]),
        encoding="utf-8",
    )
    if seed_data:
        DataStore(root / ".kairos" / "data", storage_format="jsonl").write(
            "market.ohlcv.binance.spot.btc_usdt.1m",
            (
                {
                    "time": "2026-01-01T00:00:00+00:00",
                    "kind": "bar",
                    "venue": "binance",
                    "market": "spot",
                    "source_symbol": "BTC/USDT",
                    "market_id": market_id,
                    "instrument_id": instrument_id,
                    "market_key": "binance_spot_btc_usdt",
                    "timeframe": "1m",
                    "open": "101",
                    "high": "101",
                    "low": "101",
                    "close": "101",
                    "volume": "1",
                },
            ),
            mode="replace",
        )
    config_path = root / "launch.toml"
    config_path.write_text(
        "\n".join([
            "[launch]",
            'id = "bt-1"',
            'mode = "backtest"',
            'strategy = "strategy_mod:ConfiguredStrategy"',
            "",
            "[strategy.params]",
            f'instrument_id = "{instrument_id}"',
            f'market_id = "{market_id}"',
            "",
            "[account]",
            "cash = 1000",
            'currency = "USDT"',
            *([] if fee_rate is None else [f'fee_rate = "{fee_rate}"']),
            "",
            "[backtest]",
            'launches_root = "launches"',
            'storage_format = "jsonl"',
            'price_field = "close"',
            "",
            "[backtest.market]",
            'start = "2026-01-01T00:00:00+00:00"',
            'end = "2026-01-01T00:01:00+00:00"',
            f'on_missing = "{on_missing}"',
        ])
        + "\n",
        encoding="utf-8",
    )
    return config_path
