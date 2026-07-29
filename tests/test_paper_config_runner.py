from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kairospy.application.service.modes.paper import configured_paper
from kairospy.application.system import TradingSystemLauncher
from kairospy.application.system.control.registry import RunRegistry
from kairospy.infrastructure.integrations import HyperliquidMarketDataConnector
from kairospy.surface.cli.commands.run import run_app


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)


def test_configured_paper_runs_new_engine(tmp_path) -> None:
    config_path = _write_paper_project(tmp_path)

    result = TradingSystemLauncher().run_configured_paper(configured_paper(config_path, account_resolver=_resolver(config_path)))

    assert result.mode.value == "paper"
    assert result.runtime.event_count == 2
    assert len(result.fills) == 1
    assert result.account_view.cash == result.final_equity
    run_directory = tmp_path / ".kairos" / "runs" / "paper" / "paper-1"
    assert json.loads((run_directory / "summary.json").read_text(encoding="utf-8"))["event_count"] == 2
    assert "paper strategy saw quote" in (run_directory / "run.log").read_text(encoding="utf-8")
    assert (run_directory / "account" / "current.json").exists()
    assert (run_directory / "account" / "equity.jsonl").read_text(encoding="utf-8").strip()
    records = RunRegistry(tmp_path / ".kairos" / "runs").list(mode="paper", run_id="paper-1")
    assert len(records) == 1


def test_run_paper_command_uses_new_config_runner(tmp_path) -> None:
    config_path = _write_paper_project(tmp_path)

    result = CliRunner().invoke(run_app, ["start", str(config_path), "--format", "json"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "paper strategy saw quote" in result.output
    assert '"mode": "paper"' in result.output
    assert '"event_count": 2' in result.output


def test_configured_paper_default_runs_root_uses_current_working_directory(tmp_path, monkeypatch) -> None:
    config_root = tmp_path / "configs"
    config_root.mkdir()
    config_path = _write_paper_project(config_root, runs_root=False)
    (config_root / "__init__.py").write_text("", encoding="utf-8")
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        .replace('strategy = "strategy_mod:PaperStrategy"', 'strategy = "configs.strategy_mod:PaperStrategy"')
        .replace('events = "events.jsonl"', 'events = "configs/events.jsonl"'),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    configured = configured_paper(config_path, account_resolver=_resolver(config_path))

    assert configured.run_directory == tmp_path / ".kairos" / "runs" / "paper" / "paper-1"


def test_configured_paper_can_stream_market_data_from_integration_feed(tmp_path) -> None:
    config_path = _write_streaming_paper_project(tmp_path)

    result = TradingSystemLauncher().run_configured_paper(
        configured_paper(config_path, market_feed_factory=lambda venue: FakePaperFeed(), account_resolver=_resolver(config_path))
    )

    assert result.mode.value == "paper"
    assert result.runtime.event_count == 2
    assert len(result.fills) == 1
    assert result.account_view.cash == result.final_equity
    run_directory = tmp_path / ".kairos" / "runs" / "paper" / "paper-streaming-1"
    assert json.loads((run_directory / "summary.json").read_text(encoding="utf-8"))["run_id"] == "paper-streaming-1"
    assert (run_directory / "account" / "current.json").exists()


def test_configured_paper_selects_account_id_for_streaming_feed(tmp_path) -> None:
    config_path = _write_streaming_paper_project(tmp_path, extra_accounts=True, account_id="alt")

    configured = configured_paper(config_path, market_feed_factory=lambda venue: FakePaperFeed(), account_resolver=_resolver(config_path))
    result = TradingSystemLauncher().run_configured_paper(configured)

    assert result.account.account.account_id == "alt"
    assert configured.normalized_config["account"]["cash"] == 500


def test_configured_paper_supports_hyperliquid_default_market_feed(tmp_path) -> None:
    config_path = _write_streaming_paper_project(
        tmp_path,
        venue="hyperliquid",
        market="swap",
        symbol="BTC/USDC:USDC",
        quote_currency="USDC",
    )

    configured = configured_paper(config_path, account_resolver=_resolver(config_path))

    assert isinstance(configured.market_data.feed, HyperliquidMarketDataConnector)
    assert configured.normalized_config["paper"]["source"] == "hyperliquid:swap:BTC/USDC:USDC"


def test_configured_paper_can_run_hyperliquid_streaming_feed(tmp_path) -> None:
    config_path = _write_streaming_paper_project(
        tmp_path,
        venue="hyperliquid",
        market="swap",
        symbol="BTC/USDC:USDC",
        quote_currency="USDC",
    )

    configured = configured_paper(config_path, market_feed_factory=lambda venue: FakePaperFeed(), account_resolver=_resolver(config_path))
    result = TradingSystemLauncher().run_configured_paper(configured)

    assert result.mode.value == "paper"
    assert result.runtime.event_count == 2
    assert len(result.fills) == 1
    assert result.account_view.cash == result.final_equity


def test_configured_paper_accepts_live_ticker_without_timestamp(tmp_path) -> None:
    config_path = _write_streaming_paper_project(
        tmp_path,
        venue="hyperliquid",
        market="swap",
        symbol="BTC/USDC:USDC",
        quote_currency="USDC",
    )

    result = TradingSystemLauncher().run_configured_paper(
        configured_paper(config_path, market_feed_factory=lambda venue: FakeTimestamplessFeed(), account_resolver=_resolver(config_path))
    )

    assert result.runtime.event_count == 2
    assert result.runtime.last_event.time.tzinfo is not None


class FakePaperFeed:
    async def watch_ticker(self, symbol: str, *, params: Mapping[str, object] | None = None) -> AsyncIterator[Mapping[str, object]]:
        yield {"timestamp": 1767225600000, "bid": "100", "ask": "101"}

    async def watch_order_book(
        self,
        symbol: str,
        *,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        if False:
            yield {}

    async def watch_trades(
        self,
        symbol: str,
        *,
        since: object | None = None,
        limit: int = 50,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        if False:
            yield {}


class FakeTimestamplessFeed(FakePaperFeed):
    async def watch_ticker(self, symbol: str, *, params: Mapping[str, object] | None = None) -> AsyncIterator[Mapping[str, object]]:
        yield {"last": "101", "close": "101", "info": {"price": "101"}}


def _write_paper_project(root: Path, *, runs_root: bool = True) -> Path:
    market_id = "market:binance:spot:btc_usdt"
    instrument_id = "instrument:spot:btc:usdt"
    (root / "strategy_mod.py").write_text(
        "\n".join([
            "from decimal import Decimal",
            "from kairospy.application.strategy import StrategyBase",
            "from kairospy.core.intent import target_position_intent",
            "class PaperStrategy(StrategyBase):",
            "    strategy_id = 'paper-strategy'",
            "    def __init__(self, instrument_id, market_id):",
            "        self.instrument_id = instrument_id",
            "        self.market_id = market_id",
            "        self.entered = False",
            "    def on_data(self, context, signal):",
            "        print('paper strategy saw quote')",
            "        if self.entered:",
            "            return None",
            "        self.entered = True",
            "        context.intent(target_position_intent(",
            "            strategy_id=self.strategy_id,",
            "            instrument_id=self.instrument_id,",
            "            market_id=self.market_id,",
            "            target_quantity=Decimal('1'),",
            "            at=signal.time,",
            "            intent_id='paper-intent',",
            "        ))",
        ]),
        encoding="utf-8",
    )
    (root / "events.jsonl").write_text(
        json.dumps(
            {
                "time": "2026-01-01T00:00:00+00:00",
                "kind": "quote",
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
    _write_account("main", venue="paper", cash=1000, currency="USDT")
    lines = [
            "[run]",
            'id = "paper-1"',
            'mode = "paper"',
            'strategy = "strategy_mod:PaperStrategy"',
            "",
            "[account]",
            'ref = "main"',
            "",
            "[strategy.params]",
            f'instrument_id = "{instrument_id}"',
            f'market_id = "{market_id}"',
            "",
            "[paper]",
            'events = "events.jsonl"',
            'price_field = "ask"',
    ]
    if runs_root:
        lines.append('runs_root = ".kairos/runs"')
    config_path = root / "paper.toml"
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config_path


def _write_streaming_paper_project(
    root: Path,
    *,
    extra_accounts: bool = False,
    account_id: str | None = None,
    venue: str = "binance",
    market: str = "spot",
    symbol: str = "BTC/USDT",
    quote_currency: str = "USDT",
) -> Path:
    normalized_symbol = symbol.replace("/", "_").replace(":", "_").lower()
    market_id = f"market:{venue}:{market}:{normalized_symbol}"
    instrument_id = f"instrument:{market}:btc:{quote_currency.lower()}"
    selected_account = account_id or "main"
    _write_account("main", venue=venue, cash=1000, currency=quote_currency)
    if extra_accounts:
        _write_account("alt", venue=venue, cash=500, currency=quote_currency, index=1)
    (root / "strategy_mod.py").write_text(
        "\n".join([
            "from decimal import Decimal",
            "from kairospy.application.strategy import StrategyBase",
            "from kairospy.core.intent import target_position_intent",
            "class PaperStrategy(StrategyBase):",
            "    strategy_id = 'paper-strategy'",
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
            "            target_quantity=Decimal('1'),",
            "            at=signal.time,",
            "            intent_id='paper-intent',",
            "        ))",
        ]),
        encoding="utf-8",
    )
    config_path = root / "paper-streaming.toml"
    lines = [
            "[run]",
            'id = "paper-streaming-1"',
            'mode = "paper"',
            'strategy = "strategy_mod:PaperStrategy"',
            "",
            "[account]",
            f'ref = "{selected_account}"',
            "",
            "[strategy.params]",
            f'instrument_id = "{instrument_id}"',
            f'market_id = "{market_id}"',
    ]
    lines.extend([
            "",
            "[paper]",
            f'venue = "{venue}"',
            f'market = "{market}"',
            f'symbol = "{symbol}"',
            'price_field = "ask"',
            'runs_root = ".kairos/runs"',
    ])
    config_path.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    return config_path


def _write_account(account_id: str, *, venue: str, cash: int, currency: str, index: int = 0) -> None:
    account_root = Path.cwd() / ".kairos" / "accounts"
    account_root.mkdir(parents=True, exist_ok=True)
    (account_root / f"{account_id}.toml").write_text(
        "\n".join(
            [
                "[account]",
                f'id = "{account_id}"',
                f"index = {index}",
                f'venue = "{venue}"',
                f'provider = "{venue}"',
                'environment = "paper"',
                f"cash = {cash}",
                f'currency = "{currency}"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _resolver(config_path: Path):
    return TradingSystemLauncher()._account_resolver(config_path)
