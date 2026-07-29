from __future__ import annotations

import json
from pathlib import Path
from decimal import Decimal
from typing import Mapping

import pytest
from typer.testing import CliRunner

from kairospy.application.service.modes.backtest import BacktestConfigurationError, configured_backtest
from kairospy.application.system import TradingConfigurationError, TradingSystemLauncher
from kairospy.infrastructure.data import DataStore
from kairospy.surface.cli.commands.run import run_app


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)


def test_configured_backtest_runs_new_engine_and_writes_account_journal(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)

    configured = configured_backtest(config_path)
    result = TradingSystemLauncher().run_configured_backtest(configured)

    assert result.runtime.event_count == 2
    assert len(result.fills) == 1
    assert len(result.equity_curve) == 1
    assert result.metrics.net_profit == result.net_profit
    assert result.final_equity == result.account_view.equity
    current = json.loads((configured.run_directory / "account" / "current.json").read_text(encoding="utf-8"))
    assert current["run_id"] == "bt-1"
    assert current["equity"] == str(result.final_equity)


def test_configured_backtest_applies_account_fee_rate(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path, fee_rate="0.001")

    configured = configured_backtest(config_path)
    result = TradingSystemLauncher().run_configured_backtest(configured)

    assert configured.normalized_config["account"]["fee_rate"] == Decimal("0.001")
    assert result.fills[0].fee == Decimal("0.202")
    assert result.account_view.cash == Decimal("797.798")


def test_backtest_downloads_missing_history_after_strategy_subscribe(tmp_path, monkeypatch) -> None:
    config_path = _write_backtest_project(tmp_path, seed_data=False, on_missing="download")

    monkeypatch.setattr("kairospy.application.system.facade.trading.exchange", lambda exchange_name, driver_name: FakeHistoricalClient())

    result = TradingSystemLauncher().run_backtest_config(config_path)

    assert len(result.fills) == 1
    rows = DataStore(tmp_path / ".kairos" / "data", storage_format="jsonl").read_rows("market.ohlcv.binance.spot.btc_usdt.1m")
    assert rows[0]["close"] == "101"


def test_backtest_strategy_subscribes_by_dataset_id(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path, subscribe_by_dataset=True)

    result = TradingSystemLauncher().run_backtest_config(config_path)

    assert result.runtime.event_count == 2
    assert len(result.fills) == 1


def test_run_backtest_command_writes_run_artifacts(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)

    result = CliRunner().invoke(run_app, ["start", str(config_path), "--format", "json"], catch_exceptions=False)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["run_id"] == "bt-1"
    assert payload["run_instance_id"]
    assert payload["result"]["event_count"] == 2
    run_directory = Path(payload["directory"])
    group_directory = tmp_path / ".kairos" / "runs" / "backtest" / "bt-1"
    assert run_directory == group_directory / "instances" / payload["run_instance_id"]
    assert (run_directory / "summary.json").exists()
    assert (run_directory / "account" / "current.json").exists()
    log_text = (run_directory / "run.log").read_text(encoding="utf-8")
    assert "Run Environment" in log_text
    assert "System Status" in log_text
    assert "Account Status" in log_text
    assert (run_directory / "equity.jsonl").read_text(encoding="utf-8").strip()
    assert json.loads((run_directory / "metrics.json").read_text(encoding="utf-8"))["net_profit"] == "-202"
    assert json.loads((run_directory / "summary.json").read_text(encoding="utf-8"))["event_count"] == 2
    intent_states = [json.loads(line) for line in (run_directory / "intent_states.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(intent_states) == 1
    assert intent_states[0]["intent"]["intent_id"]["value"] == "intent-1"
    assert intent_states[0]["status"] == "satisfied"
    current = json.loads((group_directory / "current.json").read_text(encoding="utf-8"))
    assert current["run_instance_id"] == payload["run_instance_id"]


def test_run_backtest_command_accepts_strategy_runtime_override(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace('strategy = "strategy_mod:ConfiguredStrategy"\n', ""),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        run_app,
        ["start", str(config_path), "--strategy", "strategy_mod:ConfiguredStrategy", "--format", "json"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["result"]["strategy_id"] == "configured-strategy"
    assert payload["result"]["event_count"] == 2


def test_run_backtest_command_creates_a_new_instance_each_start(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)

    first = CliRunner().invoke(run_app, ["start", str(config_path), "--format", "json"], catch_exceptions=False)
    second = CliRunner().invoke(run_app, ["start", str(config_path), "--format", "json"], catch_exceptions=False)

    first_payload = json.loads(first.output)
    second_payload = json.loads(second.output)
    assert first_payload["run_instance_id"] != second_payload["run_instance_id"]
    assert Path(first_payload["directory"]).exists()
    assert Path(second_payload["directory"]).exists()
    current = json.loads((tmp_path / ".kairos" / "runs" / "backtest" / "bt-1" / "current.json").read_text(encoding="utf-8"))
    assert current["run_instance_id"] == second_payload["run_instance_id"]


def test_run_backtest_command_uses_new_config_runner(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)

    result = CliRunner().invoke(run_app, ["start", str(config_path), "--format", "json"], catch_exceptions=False)

    assert result.exit_code == 0
    assert '"run_id": "bt-1"' in result.output
    assert '"event_count": 2' in result.output


def test_run_backtest_config_wraps_recipe_configuration_errors(tmp_path) -> None:
    config_path = tmp_path / "invalid.toml"
    config_path.write_text(
        "\n".join([
            "[run]",
            'id = "bad"',
            'mode = "backtest"',
            'strategy = "missing:factory"',
        ])
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(TradingConfigurationError):
        TradingSystemLauncher().run_backtest_config(config_path)


def test_configured_backtest_rejects_legacy_event_source(tmp_path) -> None:
    config_path = tmp_path / "legacy.toml"
    config_path.write_text(
        "\n".join([
            "[run]",
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


def test_run_backtest_config_does_not_wrap_strategy_runtime_value_error(tmp_path) -> None:
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
        TradingSystemLauncher().run_backtest_config(config_path)
    assert not isinstance(error.value, TradingConfigurationError)
    assert str(error.value) == "strategy runtime failed"


class FakeHistoricalClient:
    def fetch_ohlcv(
        self,
        symbol: str,
        *,
        timeframe: str = "1m",
        since: object | None = None,
        until: object | None = None,
        limit: int = 1000,
        params: Mapping[str, object] | None = None,
    ):
        return (
            {
                "time": "2026-01-01T00:00:00+00:00",
                "kind": "bar",
                "venue": "binance",
                "market": "spot",
                "source_symbol": symbol,
                "market_id": "market:binance:spot:btc_usdt",
                "instrument_id": "instrument:spot:btc:usdt",
                "market_key": "binance_spot_btc_usdt",
                "timeframe": timeframe,
                "open": "101",
                "high": "101",
                "low": "101",
                "close": "101",
                "volume": "1",
            },
        )


def _write_backtest_project(
    root: Path,
    *,
    seed_data: bool = True,
    on_missing: str = "error",
    fee_rate: str | None = None,
    subscribe_by_dataset: bool = False,
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
    config_path = root / "run.toml"
    config_path.write_text(
        "\n".join([
            "[run]",
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
            'runs_root = "runs"',
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
