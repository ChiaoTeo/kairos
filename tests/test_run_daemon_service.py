from __future__ import annotations

import json
from pathlib import Path
import time

import pytest
from typer.testing import CliRunner

from kairospy.application.system.run import RunDaemonService, RunRegistry
from kairospy.surface.products.run import run_app


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)


def test_run_daemon_service_runs_backtest_foreground_and_writes_state(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)
    root = tmp_path / "daemon-runs"

    result = RunDaemonService(root).run_foreground(mode="backtest", config_path=config_path)

    assert result.phase == "stopped"
    assert result.run_instance_id
    assert result.result["event_count"] == 2
    assert (root / "backtest" / "bt-1" / "state.json").exists()
    assert (root / "backtest" / "bt-1" / "events.jsonl").exists()
    current = json.loads((root / "backtest" / "bt-1" / "current.json").read_text(encoding="utf-8"))
    assert current["run_instance_id"] == result.run_instance_id
    assert (root / "backtest" / "bt-1" / "runs" / result.run_instance_id / "state.json").exists()
    record = RunRegistry(root).list(mode="backtest", run_id="bt-1")[0].to_dict()
    assert record["phase"] == "stopped"
    assert record["heartbeat_at"] is not None
    assert record["result"]["event_count"] == 2
    assert record["context"]["config_file"] == str(config_path)


def test_run_daemon_start_foreground_command_runs_config(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)
    root = tmp_path / "daemon-runs"

    result = CliRunner().invoke(
        run_app,
        ["daemon", "start", "--foreground", "--root", str(root), "--mode", "backtest", "--config", str(config_path)],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["phase"] == "stopped"
    assert payload["run_instance_id"]
    assert payload["result"]["event_count"] == 2
    assert (root / "backtest" / "bt-1" / "summary.json").exists()


def test_run_daemon_start_background_command_launches_config(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)
    root = tmp_path / "daemon-runs"

    result = CliRunner().invoke(
        run_app,
        ["daemon", "start", "--root", str(root), "--mode", "backtest", "--config", str(config_path)],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["phase"] == "starting"
    assert payload["run_instance_id"]
    summary_path = root / "backtest" / "bt-1" / "summary.json"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not summary_path.exists():
        time.sleep(0.05)
    assert json.loads(summary_path.read_text(encoding="utf-8"))["event_count"] == 2


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
