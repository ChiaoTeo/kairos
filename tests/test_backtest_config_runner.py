from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from kairospy.application.service.engine.backtest import configured_backtest
from kairospy.application.service.system.run import RunAccountJournal
from kairospy.surface.products.backtest import backtest_app
from kairospy.surface.products.run import run_app


def test_configured_backtest_runs_new_engine_and_writes_account_journal(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)

    configured = configured_backtest(config_path)
    result = configured.run()
    RunAccountJournal(configured.run_directory, run_id=configured.run_id, mode="backtest").record_backtest_result(result)

    assert result.runtime.event_count == 2
    assert len(result.fills) == 1
    assert result.final_equity == result.account_view.equity
    current = json.loads((configured.run_directory / "account" / "current.json").read_text(encoding="utf-8"))
    assert current["run_id"] == "bt-1"
    assert current["fills"] == 1


def test_backtest_run_command_uses_new_config_runner(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)

    result = CliRunner().invoke(backtest_app, ["--config", str(config_path)], catch_exceptions=False)

    assert result.exit_code == 0
    assert '"run_id": "bt-1"' in result.output
    assert '"fills": 1' in result.output
    run_directory = tmp_path / "runs" / "backtest" / "bt-1"
    assert (run_directory / "summary.json").exists()
    assert (run_directory / "account" / "current.json").exists()
    assert json.loads((run_directory / "summary.json").read_text(encoding="utf-8"))["event_count"] == 2


def test_run_backtest_command_uses_new_config_runner(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)

    result = CliRunner().invoke(run_app, ["backtest", "--config", str(config_path), "--format", "json"], catch_exceptions=False)

    assert result.exit_code == 0
    assert '"run_id": "bt-1"' in result.output
    assert '"event_count": 2' in result.output


def _write_backtest_project(root: Path) -> Path:
    market_id = "market:binance:spot:btc_usdt"
    instrument_id = "instrument:spot:btc:usdt"
    (root / "strategy_mod.py").write_text(
        "\n".join([
            "from decimal import Decimal",
            "from kairospy.application.strategy import StrategyBase",
            "from kairospy.core.intent import target_position_intent",
            "",
            "class ConfiguredStrategy(StrategyBase):",
            "    strategy_id = 'configured-strategy'",
            "    def __init__(self, instrument_id, market_id):",
            "        self.instrument_id = instrument_id",
            "        self.market_id = market_id",
            "        self.entered = False",
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
    (root / "events.jsonl").write_text(
        json.dumps(
            {
                "time": "2026-01-01T00:00:00+00:00",
                "kind": "quote",
                "venue": "binance",
                "market": "spot",
                "market_id": market_id,
                "instrument_id": instrument_id,
                "market_key": "binance_spot_btc_usdt",
                "bid": "100",
                "ask": "101",
            }
        )
        + "\n",
        encoding="utf-8",
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
            "",
            "[backtest]",
            'events = "events.jsonl"',
            'runs_root = "runs"',
            'storage_format = "jsonl"',
            'venue = "binance"',
            'market = "spot"',
            'price_field = "ask"',
        ])
        + "\n",
        encoding="utf-8",
    )
    return config_path
