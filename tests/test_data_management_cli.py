from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping

from typer.testing import CliRunner

from kairospy.core.market import Bar
from kairospy.infrastructure.persistence.market_data.catalog import DataStore
from kairospy.surface.cli.commands.market import market_app


def test_market_cli_lists_inspects_aliases_and_prunes(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    data_root = tmp_path / ".kairos" / "data"
    store = DataStore(data_root, storage_format="jsonl")
    store.write(
        "market.ohlcv.binance.spot.btc_usdt.1m",
        (
            {"time": "2026-01-01T00:00:00+00:00", "close": "100"},
            {"time": "2026-01-01T00:01:00+00:00", "close": "101"},
        ),
        mode="replace",
    )

    read_text = CliRunner().invoke(
        market_app,
        ["dataset", "read", "market.ohlcv.binance.spot.btc_usdt.1m", "--output", "text", "--limit", "1"],
        catch_exceptions=False,
    )
    read_jsonl = CliRunner().invoke(
        market_app,
        ["dataset", "read", "market.ohlcv.binance.spot.btc_usdt.1m", "--output", "jsonl", "--limit", "1"],
        catch_exceptions=False,
    )

    listed = CliRunner().invoke(market_app, ["dataset", "list", "--format", "jsonl", "--output", "json"], catch_exceptions=False)
    inspected = CliRunner().invoke(
        market_app,
        ["dataset", "inspect", "market.ohlcv.binance.spot.btc_usdt.1m", "--format", "jsonl", "--output", "json"],
        catch_exceptions=False,
    )
    aliased = CliRunner().invoke(
        market_app,
        ["dataset", "alias", "market.ohlcv.binance.spot.btc_usdt.1m", "btc-bars", "--format", "jsonl"],
        catch_exceptions=False,
    )
    pruned = CliRunner().invoke(
        market_app,
        [
            "dataset",
            "prune",
            "btc-bars",
            "--start",
            "2026-01-01T00:00:00+00:00",
            "--end",
            "2026-01-01T00:01:00+00:00",
            "--format",
            "jsonl",
            "--output",
            "json",
        ],
        catch_exceptions=False,
    )

    assert read_text.exit_code == 0
    assert read_text.output.startswith("Result\n")
    assert "2026-01-01T00:00:00+00:00" in read_text.output
    assert read_jsonl.exit_code == 0
    assert json.loads(read_jsonl.output)["close"] == "100"
    assert listed.exit_code == 0
    assert "market.ohlcv.binance.spot.btc_usdt.1m" in json.loads(listed.output)["datasets"]
    assert json.loads(inspected.output)["rows"] == 2
    assert json.loads(aliased.output)["alias"] == "btc-bars"
    assert json.loads(pruned.output)["deleted_rows"] == 1
    operations = (tmp_path / ".kairos" / "state" / "operations.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(operations[-2])["action"] == "market.alias"
    assert json.loads(operations[-1])["action"] == "market.prune"


def test_market_cli_reports_capabilities() -> None:
    result = CliRunner().invoke(market_app, ["source", "capabilities", "--exchange", "binance", "--output", "json"], catch_exceptions=False)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    by_market = {item["market"]: item for item in payload["markets"]}
    assert by_market["spot"]["venue"] == "binance"
    assert by_market["spot"]["historical"][0]["kind"] == "ohlcv"
    assert {item["kind"] for item in by_market["spot"]["live"]} == {"ticker", "orderbook", "trades"}
    assert by_market["option"]["status"] == "configured"
    assert {item["kind"] for item in by_market["option"]["live"]} == {"ticker", "orderbook", "trades", "option_greeks"}


def test_market_cli_checks_specific_market_data_subscription() -> None:
    valid = CliRunner().invoke(
        market_app,
        ["source", "check", "--exchange", "binance", "--market", "spot", "--symbol", "BTC/USDT", "--kind", "bar", "--timeframe", "1m", "--output", "json"],
        catch_exceptions=False,
    )
    invalid = CliRunner().invoke(
        market_app,
        ["source", "check", "--exchange", "binance", "--market", "option", "--symbol", "BTC/USDT", "--kind", "bar", "--timeframe", "1m", "--output", "json"],
        catch_exceptions=False,
    )

    assert json.loads(valid.output)["valid"] is True
    assert json.loads(valid.output)["dataset"] == "market.ohlcv.binance.spot.btc_usdt.1m"
    assert json.loads(invalid.output)["valid"] is False
    assert "not supported" in json.loads(invalid.output)["reason"]


def test_market_cli_prefetches_backtest_strategy_subscriptions(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    config_path = _write_backtest_project(tmp_path)

    monkeypatch.setattr("kairospy.application.support.system.facade.market.exchange", lambda exchange_name, driver_name: FakeHistoricalClient())

    result = CliRunner().invoke(market_app, ["data", "prefetch", str(config_path), "--output", "json", "--limit", "10"], catch_exceptions=False)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["count"] == 1
    assert payload["downloads"][0]["dataset"] == "market.ohlcv.binance.spot.btc_usdt.1m"
    rows = DataStore(tmp_path / ".kairos" / "data", storage_format="jsonl").read_rows("market.ohlcv.binance.spot.btc_usdt.1m")
    assert rows[0]["close"] == "100.5"


def test_market_cli_prefetch_dry_launch_plans_without_downloading(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    config_path = _write_backtest_project(tmp_path)

    result = CliRunner().invoke(market_app, ["data", "prefetch", str(config_path), "--dry-run", "--output", "json"], catch_exceptions=False)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["plan"][0]["dataset"] == "market.ohlcv.binance.spot.btc_usdt.1m"
    assert payload["downloads"] == []
    assert not (tmp_path / ".kairos" / "data" / "datasets").exists()


def test_market_cli_persists_live_stream_by_dataset_id(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    monkeypatch.setattr("kairospy.application.support.system.facade.market.exchange", lambda exchange_name, driver_name: FakeHistoricalClient())

    result = CliRunner().invoke(
        market_app,
        ["stream", "persist", "market.trades.binance.spot.btc_usdt", "--limit", "1", "--format", "jsonl"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert result.output.strip() == "1"
    rows = DataStore(tmp_path / ".kairos" / "data", storage_format="jsonl").read_rows("market.trades.binance.spot.btc_usdt")
    assert rows[0]["price"] == "100.5"


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
        _ = symbol, since, until, limit, adapter_options
        return (
            Bar(
                instrument_id="instrument:spot:btc:usdt",
                market_id="market:binance:spot:btc_usdt",
                market_key="binance_spot_btc_usdt",
                time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                timeframe=timeframe,
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100.5"),
                volume=Decimal("10"),
                source="binance",
            ),
        )

    async def watch_trades(
        self,
        symbol: str,
        *,
        since: object | None = None,
        limit: int = 50,
        params: Mapping[str, object] | None = None,
    ):
        yield {
            "time": "2026-01-01T00:00:00+00:00",
            "kind": "trade",
            "venue": "binance",
            "market": "spot",
            "source_symbol": symbol,
            "price": "100.5",
            "amount": "1",
        }


def _write_backtest_project(root) -> object:
    market_id = "market:binance:spot:btc_usdt"
    instrument_id = "instrument:spot:btc:usdt"
    (root / "strategy_mod.py").write_text(
        "\n".join([
            "from kairospy.application.usecases.strategy.protocol import StrategyBase",
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
        ])
        + "\n",
        encoding="utf-8",
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
            "[backtest]",
            'storage_format = "jsonl"',
            "",
            "[backtest.market]",
            'start = "2026-01-01T00:00:00+00:00"',
            'end = "2026-01-01T00:01:00+00:00"',
            'on_missing = "error"',
        ])
        + "\n",
        encoding="utf-8",
    )
    return config_path


def _write_workspace_manifest(root) -> None:
    kairos = root / ".kairos"
    kairos.mkdir(parents=True, exist_ok=True)
    (kairos / "kairos.toml").write_text("[project]\nname = \"test\"\n[data]\nstorage_format = \"jsonl\"\n", encoding="utf-8")
