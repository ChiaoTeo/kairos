from __future__ import annotations

import json
from io import StringIO

from rich.console import Console

from kairospy.data import DataStore
from kairospy.core.reference import LifecycleEvent, LifecycleEventType
from kairospy.config import KairosConfig
from kairospy.surface.app import PRODUCTS, AppSession, ProductMaturity, product_for_token
from kairospy.surface.render_text import render_product_registry
from kairospy.surface.tui import RichTui, TextTui
from kairospy.surface import cli
from kairospy.surface.products import data as data_product
from kairospy.surface.products import reference as reference_product
from kairospy.surface.products import streams as streams_product
from kairospy.surface.state import SurfaceContext, render_run_strip, render_surface_overview


class FakeBinance:
    def __init__(self, driver):
        self.driver = driver

    def fetch_ohlcv(self, symbol, *, timeframe="1m", since=None, until=None, limit=1000, params=None):
        assert symbol == "BTC/USDT"
        assert timeframe == "1m"
        assert limit == 1000
        return [
            {
                "time": "2026-01-01T00:00:00+00:00",
                "symbol": symbol,
                "timeframe": timeframe,
                "close": "100",
            },
            {
                "time": "2026-01-01T00:01:00+00:00",
                "symbol": symbol,
                "timeframe": timeframe,
                "close": "101",
            },
        ]

    async def watch_ticker(self, symbol, *, params=None):
        assert symbol == "BTC/USDT"
        yield {
            "time": "2026-01-01T00:00:00+00:00",
            "symbol": symbol,
            "last": "100",
            "params": params,
        }

    async def watch_trades(self, symbol, *, since=None, limit=50, params=None):
        assert symbol == "BTC/USDT"
        assert limit == 1
        yield {
            "time": "2026-01-01T00:00:00+00:00",
            "symbol": symbol,
            "id": "1",
            "price": "100",
        }

    def fetch_markets(self, *, params=None):
        assert params == {"type": "spot"}
        return (
            {
                "venue": "binance",
                "market": "spot",
                "source_symbol": "BTC/USDT",
                "base": "BTC",
                "quote": "USDT",
                "active": True,
            },
        )

    def fetch_delist_events(self, *, catalog=None, market="spot", params=None):
        resolved = catalog.resolve_market("BTC/USDT", venue="binance", market=market, at=_time("2026-01-01T00:00:00+00:00"))
        return (
            LifecycleEvent(
                LifecycleEventType.DELISTED,
                _time("2026-01-02T00:00:00+00:00"),
                instrument_id=resolved.instrument_id,
                listing_id=resolved.listing_id,
                market_id=resolved.market_id,
                venue="binance",
                source_symbol="BTC/USDT",
                current={"scheduled": True, "market": market},
            ),
        )


class FakeHyperliquid(FakeBinance):
    def fetch_markets(self, *, params=None):
        assert params is None
        return (
            {
                "venue": "hyperliquid",
                "market": "swap",
                "source_symbol": "BTC/USDC:USDC",
                "base": "BTC",
                "quote": "USDC",
                "active": True,
            },
            {
                "venue": "hyperliquid",
                "market": "spot",
                "source_symbol": "PURR/USDC",
                "base": "PURR",
                "quote": "USDC",
                "active": True,
            },
        )


class FakeMassiveProvider:
    def fetch_markets(self, *, params=None):
        assert params == {"asset_class": "equity"}
        return (
            {
                "venue": "nasdaq",
                "ticker": "AAPL",
                "name": "Apple Inc.",
                "cik": "320193",
                "currency": "USD",
                "active": True,
            },
        )

    def fetch_lifecycle_events(self, ticker, *, start, end, catalog, venue=None):
        market = catalog.resolve_market(ticker, venue=venue or "nasdaq", market="equity", at=start)
        return (
            LifecycleEvent(
                LifecycleEventType.DIVIDEND,
                start,
                instrument_id=market.instrument_id,
                listing_id=market.listing_id,
                market_id=market.market_id,
                venue=market.venue,
                source_symbol=market.source_symbol,
                current={"amount_per_share": "0.26", "currency": "USD", "pay_date": end.isoformat()},
            ),
        )


class FakeDriver:
    pass


def _patch_integration(monkeypatch) -> None:
    monkeypatch.setattr(data_product, "exchange", lambda exchange_name, driver_name: FakeBinance(driver_name))
    monkeypatch.setattr(streams_product, "exchange", lambda exchange_name, driver_name: FakeBinance(driver_name))
    monkeypatch.setattr(
        reference_product,
        "exchange",
        lambda exchange_name, driver_name: (
            FakeHyperliquid(driver_name) if exchange_name.value == "hyperliquid" else FakeBinance(driver_name)
        ),
    )
    monkeypatch.setattr(reference_product, "provider", lambda provider_name, driver_name: FakeMassiveProvider())


def _run(argv: list[str], stdout: StringIO) -> int:
    return cli.execute_argv(argv, stdout)


def test_cli_help_hides_experimental_tui_command() -> None:
    stdout = StringIO()

    assert _run(["--help"], stdout) == 0

    text = stdout.getvalue()
    assert "data" in text
    assert "streams" in text
    assert "tui" not in text


def test_cli_product_shell_shows_product_menu() -> None:
    stdout = StringIO()

    result = _run(["shell", "--command", "menu"], stdout)

    assert result == 0
    text = stdout.getvalue()
    assert "Kairos" in text
    assert "runs" in text
    assert "Products" in text
    assert "run" in text
    assert "data" in text


def test_app_registry_marks_product_maturity() -> None:
    text = render_product_registry(PRODUCTS)

    assert "run" in text
    assert "workspace" in text
    assert product_for_token("1").name == "run"
    assert product_for_token("run").maturity is ProductMaturity.WORKSPACE
    assert product_for_token("reference").maturity is ProductMaturity.PANEL
    assert product_for_token("data").maturity is ProductMaturity.RAW


def test_cli_app_home_shows_migration_layers() -> None:
    stdout = StringIO()

    result = _run(["app", "--command", "help"], stdout)

    assert result == 0
    text = stdout.getvalue()
    assert "Products" in text
    assert "maturity" in text
    assert "workspace" in text
    assert "raw" in text


def test_cli_app_opens_run_workspace() -> None:
    stdout = StringIO()

    result = _run(["app", "--command", "run"], stdout)

    assert result == 0
    text = stdout.getvalue()
    assert "Recent Runs" in text
    assert "Run Workspace" in text
    assert "use <#>" in text


def test_cli_app_raw_product_bridges_to_cli() -> None:
    stdout = StringIO()

    result = _run(["app", "--command", "data", "--command", "help"], stdout)

    assert result == 0
    text = stdout.getvalue()
    assert "Raw Command Bridge" in text
    assert "Historical data commands" in text


def test_cli_app_opens_reference_panel() -> None:
    stdout = StringIO()

    result = _run(["app", "--command", "reference"], stdout)

    assert result == 0
    text = stdout.getvalue()
    assert "Reference" in text
    assert "maturity  panel" in text
    assert "Catalog" in text
    assert "Reference Panel" in text
    assert "list [filters]" in text


def test_text_tui_uses_app_core() -> None:
    stdout = StringIO()
    tui = TextTui(stdout=stdout, command_executor=lambda argv: (0, ""))

    assert tui.handle("reference") is False

    assert tui.prompt() == "kairos/app/reference> "
    assert "Reference Panel" in stdout.getvalue()


def test_rich_tui_renders_home_panel() -> None:
    console = Console(record=True, width=100, force_terminal=False)
    tui = RichTui(console=console, command_executor=lambda argv: (0, ""))

    console.print(tui.render())
    text = console.export_text()

    assert "Kairos" in text
    assert "Experimental TUI preview" in text
    assert "Products" in text
    assert "workspace" in text


def test_rich_tui_handles_reference_panel() -> None:
    console = Console(record=True, width=100, force_terminal=False)
    tui = RichTui(console=console, command_executor=lambda argv: (0, ""))

    assert tui.handle("reference") is False
    console.print(tui.render())
    text = console.export_text()

    assert tui.prompt() == "kairos/app/reference> "
    assert "Reference Panel" in text
    assert "Catalog" in text or "Metric" in text


def test_cli_product_shell_refreshes_application_status() -> None:
    stdout = StringIO()

    result = _run(["shell", "--command", "refresh"], stdout)

    assert result == 0
    text = stdout.getvalue()
    assert "Kairos" in text
    assert "Runs" in text


def test_surface_context_summarizes_run_state(monkeypatch, tmp_path) -> None:
    from kairospy.surface import state as surface_state

    class FakeStatus:
        def to_dict(self):
            return {
                "mode": "paper",
                "run_id": "demo",
                "status": "running",
                "phase": "running",
                "heartbeat_age_seconds": 1.25,
                "context": {"strategy": "examples.strategy"},
                "log_file": str(tmp_path / "daemon.log"),
            }

    config = KairosConfig(None, tmp_path, {"project": {"name": "Demo"}})
    monkeypatch.setattr(surface_state, "list_run_daemons", lambda *, stale_after_seconds: (FakeStatus(),))
    context = SurfaceContext(config=config)

    snapshot = context.snapshot()

    assert snapshot.project_name == "Demo"
    assert len(snapshot.active_runs) == 1
    assert "1 active / 1 total" in render_surface_overview(snapshot)
    assert "#  mode" in render_run_strip(snapshot)
    assert "1  paper" in render_run_strip(snapshot)
    assert "examples.strategy" in render_run_strip(snapshot)


def test_cli_product_shell_selects_numbered_run_from_overview(monkeypatch, tmp_path) -> None:
    from kairospy.surface import state as surface_state

    class FakeStatus:
        def to_dict(self):
            return {
                "mode": "paper",
                "run_id": "demo",
                "status": "running",
                "phase": "running",
                "heartbeat_age_seconds": 1.25,
                "context": {"strategy": "examples.strategy"},
                "log_file": str(tmp_path / "daemon.log"),
            }

    monkeypatch.setattr(surface_state, "list_run_daemons", lambda *, stale_after_seconds: (FakeStatus(),))
    stdout = StringIO()

    result = _run(["shell", "--command", "run", "--command", "use 1"], stdout)

    assert result == 0
    assert "Using paper run demo" in stdout.getvalue()


def test_cli_app_run_prompt_reflects_selected_run(monkeypatch, tmp_path) -> None:
    from kairospy.surface import state as surface_state

    class FakeStatus:
        def to_dict(self):
            return {
                "mode": "paper",
                "run_id": "demo",
                "status": "running",
                "phase": "running",
                "heartbeat_age_seconds": 1.25,
                "context": {"strategy": "examples.strategy"},
                "log_file": str(tmp_path / "daemon.log"),
            }

    monkeypatch.setattr(surface_state, "list_run_daemons", lambda *, stale_after_seconds: (FakeStatus(),))
    stdout = StringIO()
    session = AppSession(stdout=stdout, command_executor=lambda argv: (0, ""))

    assert session.handle("run") is False
    assert session.handle("use 1") is False

    assert session.prompt() == "kairos/app/run[paper:demo]> "
    assert "Using paper run demo" in stdout.getvalue()


def test_cli_product_shell_enters_run_context() -> None:
    stdout = StringIO()

    result = _run([
        "shell",
        "--command",
        "run",
        "--command",
        "list --mode paper",
    ], stdout)

    assert result == 0
    text = stdout.getvalue()
    assert "Run Workspace" in text
    assert "use <#>" in text
    assert "Recent Runs" in text
    assert "1  use paper" not in text


def test_cli_product_shell_runs_product_commands_without_prefix() -> None:
    stdout = StringIO()

    result = _run([
        "shell",
        "--command",
        "data",
        "--command",
        "--help",
    ], stdout)

    assert result == 0
    text = stdout.getvalue()
    assert "data product" in text
    assert "Historical data commands" in text


def test_cli_downloads_historical_data(monkeypatch, tmp_path) -> None:
    _patch_integration(monkeypatch)
    stdout = StringIO()

    result = _run([
        "data",
        "download",
        "--root",
        str(tmp_path),
        "--format",
        "jsonl",
        "--symbol",
        "BTC/USDT",
        "--dataset",
        "market.ohlcv.binance.btc_usdt.1m",
    ], stdout)

    assert result == 0
    assert (tmp_path / "datasets" / "market" / "ohlcv" / "binance" / "btc_usdt" / "1m" / "data.jsonl").exists()


def test_cli_reads_historical_data(tmp_path) -> None:
    DataStore(tmp_path, storage_format="jsonl").write(
        "market.ohlcv.binance.btc_usdt.1m",
        [{"time": "2026-01-01T00:00:00+00:00", "close": "100"}],
    )
    stdout = StringIO()
    result = _run([
        "data",
        "read",
        "--root",
        str(tmp_path),
        "--format",
        "jsonl",
        "market.ohlcv.binance.btc_usdt.1m",
    ], stdout)

    assert result == 0
    assert json.loads(stdout.getvalue())["close"] == "100"


def test_cli_replays_historical_data_without_delay(tmp_path) -> None:
    DataStore(tmp_path, storage_format="jsonl").write(
        "market.trades.binance.btc_usdt",
        [
            {"time": "2026-01-01T00:00:00+00:00", "price": "100"},
            {"time": "2026-01-01T00:00:01+00:00", "price": "101"},
        ],
    )
    stdout = StringIO()
    result = _run([
        "data",
        "replay",
        "--root",
        str(tmp_path),
        "--format",
        "jsonl",
        "--speed",
        "0",
        "market.trades.binance.btc_usdt",
    ], stdout)

    assert result == 0
    assert [json.loads(line)["price"] for line in stdout.getvalue().splitlines()] == ["100", "101"]


def test_cli_runs_dataset_backtest_and_writes_artifacts(tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    strategy_path = project / "sample_strategy.py"
    strategy_path.write_text(
        "\n".join([
            "from decimal import Decimal",
            "from kairospy.strategy import StrategyBase",
            "",
            "class BuyAndHold(StrategyBase):",
            '    strategy_id = "buy-and-hold"',
            "    def __init__(self, symbol='BTC/USDT', quantity='1'):",
            "        self.symbol = symbol",
            "        self.quantity = Decimal(str(quantity))",
            "        self.entered = False",
            "    def on_market(self, context, signal):",
            "        if self.entered:",
            "            return ()",
            "        context.target_position(self.symbol, self.quantity, intent_id='enter')",
            "        self.entered = True",
            "        return ()",
        ]),
        encoding="utf-8",
    )
    DataStore(project / ".kairos" / "data", storage_format="jsonl").write(
        "market.ohlcv.binance_spot_btc_usdt.1m",
        [
            {
                "time": "2026-01-01T00:00:00+00:00",
                "kind": "bar",
                "market_id": "market:binance:spot:btc_usdt",
                "instrument_id": "instrument:spot:btc:usdt",
                "market_key": "binance_spot_btc_usdt",
                "close": "100",
            },
            {
                "time": "2026-01-01T00:01:00+00:00",
                "kind": "bar",
                "market_id": "market:binance:spot:btc_usdt",
                "instrument_id": "instrument:spot:btc:usdt",
                "market_key": "binance_spot_btc_usdt",
                "close": "110",
            },
        ],
    )
    config_path = project / "backtest.toml"
    config_path.write_text(
        "\n".join([
            "[run]",
            'id = "sample-backtest"',
            'mode = "backtest"',
            'strategy = "sample_strategy:BuyAndHold"',
            "",
            "[strategy.params]",
            'symbol = "BTC/USDT"',
            'quantity = "1"',
            "",
            "[backtest]",
            'dataset = "market.ohlcv.binance_spot_btc_usdt.1m"',
            'storage_format = "jsonl"',
            'venue = "binance"',
            'market = "spot"',
            'price_field = "close"',
            "",
            "[account]",
            'cash = "1000"',
            'currency = "USDT"',
        ]),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    stdout = StringIO()

    result = _run(["backtest", "run", "--config", str(config_path)], stdout)

    assert result == 0
    payload = json.loads(stdout.getvalue())
    assert payload["run_id"] == "sample-backtest"
    assert payload["fills"] == 1
    run_directory = project / ".kairos" / "runs" / "backtest" / "sample-backtest"
    assert (run_directory / "summary.json").exists()
    assert (run_directory / "fills.jsonl").exists()
    assert (run_directory / "report.md").exists()


def test_cli_prints_stream_data(monkeypatch) -> None:
    _patch_integration(monkeypatch)
    stdout = StringIO()
    result = _run([
        "streams",
        "print",
        "--kind",
        "ticker",
        "--symbol",
        "BTC/USDT",
        "--limit",
        "1",
        "--poll-seconds",
        "0",
    ], stdout)

    assert result == 0
    row = json.loads(stdout.getvalue())
    assert row["last"] == "100"
    assert row["params"]["max_events"] == 1


def test_cli_persists_stream_data(monkeypatch, tmp_path) -> None:
    _patch_integration(monkeypatch)
    stdout = StringIO()
    result = _run([
        "streams",
        "persist",
        "--root",
        str(tmp_path),
        "--format",
        "jsonl",
        "--kind",
        "trades",
        "--symbol",
        "BTC/USDT",
        "--trade-limit",
        "1",
        "--limit",
        "1",
        "--dataset",
        "market.trades.binance.btc_usdt",
    ], stdout)

    assert result == 0
    rows = DataStore(tmp_path, storage_format="jsonl").read_rows("market.trades.binance.btc_usdt")
    assert stdout.getvalue() == "1\n"
    assert rows[0]["id"] == "1"


def test_cli_reads_project_kairos_config(monkeypatch, tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "kairos.toml").write_text(
        "\n".join([
            "schema_version = 1",
            "",
            "[project]",
            'name = "configured-project"',
            "",
            "[paths]",
            'lake_root = "configured-data"',
            "",
            "[data]",
            'storage_format = "jsonl"',
        ]),
        encoding="utf-8",
    )
    DataStore(project / "configured-data", storage_format="jsonl").write(
        "market.ohlcv.binance.btc_usdt.1m",
        [{"time": "2026-01-01T00:00:00+00:00", "close": "222"}],
    )
    monkeypatch.chdir(project)
    stdout = StringIO()

    result = _run(["data", "read", "market.ohlcv.binance.btc_usdt.1m"], stdout)

    assert result == 0
    assert json.loads(stdout.getvalue())["close"] == "222"


def test_cli_falls_back_to_user_kairos_config(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    work = tmp_path / "work"
    home_config = home / ".kairos"
    home_config.mkdir(parents=True)
    work.mkdir()
    (home_config / "kairos.toml").write_text(
        "\n".join([
            "schema_version = 1",
            "",
            "[paths]",
            'lake_root = "user-data"',
            "",
            "[data]",
            'storage_format = "jsonl"',
        ]),
        encoding="utf-8",
    )
    DataStore(home / ".kairos" / "user-data", storage_format="jsonl").write(
        "market.ohlcv.binance.btc_usdt.1m",
        [{"time": "2026-01-01T00:00:00+00:00", "close": "333"}],
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.chdir(work)
    stdout = StringIO()

    result = _run(["data", "read", "market.ohlcv.binance.btc_usdt.1m"], stdout)

    assert result == 0
    assert json.loads(stdout.getvalue())["close"] == "333"


def test_cli_validates_run_config(tmp_path) -> None:
    path = tmp_path / "backtest.toml"
    path.write_text(
        "\n".join([
            "[run]",
            'id = "sample-backtest"',
            'mode = "backtest"',
            'strategy = "strategies.sample:SampleStrategy"',
        ]),
        encoding="utf-8",
    )
    stdout = StringIO()

    result = _run(["run", "config", "validate", str(path)], stdout)

    assert result == 0
    payload = json.loads(stdout.getvalue())
    assert payload["valid"] is True
    assert payload["issues"] == []


def test_cli_refreshes_and_queries_reference_catalog(monkeypatch, tmp_path) -> None:
    _patch_integration(monkeypatch)
    stdout = StringIO()

    result = _run([
        "reference",
        "refresh-binance",
        "--root",
        str(tmp_path / "reference"),
        "--market",
        "spot",
        "--as-of",
        "2026-01-01T00:00:00+00:00",
    ], stdout)

    assert result == 0
    summary = json.loads(stdout.getvalue())
    assert summary["events"] == 1
    assert summary["scheduled_events"] == 1

    stdout = StringIO()
    result = _run([
        "reference",
        "markets",
        "--root",
        str(tmp_path / "reference"),
        "--venue",
        "binance",
        "--market",
        "spot",
        "--as-of",
        "2026-01-01T00:00:00+00:00",
    ], stdout)

    assert result == 0
    assert json.loads(stdout.getvalue())["source_symbol"] == "BTC/USDT"

    stdout = StringIO()
    result = _run([
        "reference",
        "events",
        "--root",
        str(tmp_path / "reference"),
    ], stdout)

    assert result == 0
    event_rows = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [row["event_type"] for row in event_rows] == ["listed", "delisted"]


def test_cli_refreshes_massive_equity_reference_catalog(monkeypatch, tmp_path) -> None:
    _patch_integration(monkeypatch)
    stdout = StringIO()

    result = _run([
        "reference",
        "refresh-massive-equities",
        "--root",
        str(tmp_path / "reference"),
        "--as-of",
        "2026-01-01T00:00:00+00:00",
    ], stdout)

    assert result == 0
    assert json.loads(stdout.getvalue())["events"] == 1

    stdout = StringIO()
    result = _run([
        "reference",
        "markets",
        "--root",
        str(tmp_path / "reference"),
        "--venue",
        "nasdaq",
        "--market",
        "equity",
        "--as-of",
        "2026-01-01T00:00:00+00:00",
    ], stdout)

    assert result == 0
    assert json.loads(stdout.getvalue())["source_symbol"] == "AAPL"


def test_cli_refreshes_hyperliquid_reference_catalog(monkeypatch, tmp_path) -> None:
    _patch_integration(monkeypatch)
    reference_root = tmp_path / "reference"
    stdout = StringIO()

    result = _run([
        "reference",
        "refresh",
        "hyperliquid",
        "--root",
        str(reference_root),
        "--market",
        "swap",
        "--as-of",
        "2026-01-01T00:00:00+00:00",
    ], stdout)

    assert result == 0
    summary = json.loads(stdout.getvalue())
    assert summary["venue"] == "hyperliquid"
    assert summary["market"] == "swap"
    assert summary["current_markets"] == 1
    assert (reference_root / "reference.sqlite").exists()

    stdout = StringIO()
    result = _run([
        "reference",
        "markets",
        "--root",
        str(reference_root),
        "--venue",
        "hyperliquid",
        "--market",
        "swap",
        "--as-of",
        "2026-01-01T00:00:00+00:00",
    ], stdout)

    assert result == 0
    assert json.loads(stdout.getvalue())["source_symbol"] == "BTC/USDC:USDC"

    stdout = StringIO()
    result = _run([
        "reference",
        "list",
        "--root",
        str(reference_root),
        "--venue",
        "hyperliquid",
        "--market",
        "swap",
        "--active-only",
        "--limit",
        "1",
        "--as-of",
        "2026-01-01T00:00:00+00:00",
    ], stdout)

    assert result == 0
    rows = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [row["source_symbol"] for row in rows] == ["BTC/USDC:USDC"]


def test_cli_syncs_massive_corporate_actions(monkeypatch, tmp_path) -> None:
    _patch_integration(monkeypatch)
    reference_root = tmp_path / "reference"
    stdout = StringIO()

    result = _run([
        "reference",
        "refresh-massive-equities",
        "--root",
        str(reference_root),
        "--as-of",
        "2026-01-01T00:00:00+00:00",
    ], stdout)

    assert result == 0

    stdout = StringIO()
    result = _run([
        "reference",
        "sync-massive-actions",
        "--root",
        str(reference_root),
        "--ticker",
        "AAPL",
        "--venue",
        "nasdaq",
        "--start",
        "2026-01-01T00:00:00+00:00",
        "--end",
        "2026-02-01T00:00:00+00:00",
    ], stdout)

    assert result == 0
    assert json.loads(stdout.getvalue())["events"] == 1

    stdout = StringIO()
    result = _run(["reference", "events", "--root", str(reference_root)], stdout)

    assert result == 0
    event_rows = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [row["event_type"] for row in event_rows] == ["listed", "dividend"]


def _time(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value)
