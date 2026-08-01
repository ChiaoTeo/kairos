from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from decimal import Decimal
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kairospy.application.usecases.market.subscriptions import MarketDataSubscriptionSpec
from kairospy.application.support.launch.composition.integrations import configured_market_feed_for_subscription
from kairospy.application.support.launch.config.paper import PaperConfigurationError, configured_paper
from kairospy.application.support.launch.launcher import TradingSystemLauncher
from kairospy.application.support.launch.control.registry import LaunchRegistry
from kairospy.infrastructure.integrations.connectors.broker.binance import BinanceEquityMarketDataConnector
from kairospy.infrastructure.integrations.connectors.exchange.hyperliquid import HyperliquidMarketDataConnector
from kairospy.core.market import Quote
from kairospy.core.reference import MarketRef
from kairospy.infrastructure.integrations.payloads.ccxt_market import ccxt_ticker_update
from kairospy.surface.cli.commands.launch import launch_app


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)


def test_configured_paper_launches_new_engine(tmp_path) -> None:
    config_path = _write_paper_project(tmp_path)

    result = TradingSystemLauncher().launch_configured_paper(configured_paper(config_path, account_resolver=_resolver(config_path)))

    assert result.mode.value == "paper"
    assert result.runtime.event_count == 2
    assert len(result.fills) == 1
    assert result.account_view.cash == result.final_equity
    launch_directory = tmp_path / ".kairos" / "launches" / "paper" / "paper-1"
    assert json.loads((launch_directory / "summary.json").read_text(encoding="utf-8"))["event_count"] == 2
    assert "paper strategy saw quote" in (launch_directory / "launch.log").read_text(encoding="utf-8")
    assert (launch_directory / "account" / "current.json").exists()
    assert not (launch_directory / "account" / "equity.jsonl").exists()
    records = LaunchRegistry(tmp_path / ".kairos" / "launches").list(mode="paper", launch_id="paper-1")
    assert len(records) == 1


def test_launch_paper_command_uses_new_config_runner(tmp_path) -> None:
    (tmp_path / ".kairos").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".kairos" / "kairos.toml").write_text("[project]\nname = \"test\"\n", encoding="utf-8")
    config_path = _write_paper_project(tmp_path)

    result = CliRunner().invoke(launch_app, ["start", str(config_path), "--format", "json"], catch_exceptions=False)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["mode"] == "paper"
    assert payload["launch_instance_id"]
    assert payload["result"]["event_count"] == 2
    launch_directory = Path(payload["directory"])
    assert "paper strategy saw quote" in (launch_directory / "launch.log").read_text(encoding="utf-8")
    assert (launch_directory / "account" / "current.json").exists()


def test_configured_paper_default_launches_root_uses_current_working_directory(tmp_path, monkeypatch) -> None:
    config_root = tmp_path / "configs"
    config_root.mkdir()
    config_path = _write_paper_project(config_root, launches_root=False)
    (config_root / "__init__.py").write_text("", encoding="utf-8")
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        .replace('strategy = "strategy_mod:PaperStrategy"', 'strategy = "configs.strategy_mod:PaperStrategy"')
        .replace('events = "events.jsonl"', 'events = "configs/events.jsonl"'),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    configured = configured_paper(config_path, market_feed_resolver_builder=_paper_feed_resolver_builder(), account_resolver=_resolver(config_path))

    assert configured.launch_directory == tmp_path / ".kairos" / "launches" / "paper" / "paper-1"


def test_configured_paper_accepts_launch_account_references(tmp_path) -> None:
    config_path = _write_paper_project(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace('[account]\nref = "main"\n', '[accounts.account1]\nref = "main"\nbooks = ["spot", "funding"]\n'),
        encoding="utf-8",
    )

    configured = configured_paper(config_path, market_feed_resolver_builder=_paper_feed_resolver_builder(), account_resolver=_resolver(config_path))
    result = TradingSystemLauncher().launch_configured_paper(configured)

    assert configured.account_config.account_id == "main"
    assert configured.normalized_config["accounts"]["account1"]["books"] == ["spot", "funding"]
    assert result.views.require("account.books").total_count == 2
    assert result.views.require("account.capabilities").total_count == 2
    assert result.views.require("account.capabilities").capabilities[1].can_trade is False


def test_configured_paper_launch_account_without_books_defaults_to_all_broker_books(tmp_path) -> None:
    config_path = _write_paper_project(tmp_path, account_venue="binance")
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace('[account]\nref = "main"\n', '[accounts.main]\nref = "main"\n'),
        encoding="utf-8",
    )

    configured = configured_paper(config_path, market_feed_resolver_builder=_paper_feed_resolver_builder(), account_resolver=_resolver(config_path))
    result = TradingSystemLauncher().launch_configured_paper(configured)

    assert [book.book_kind for book in result.views.require("account.books").books] == [
        "spot",
        "equity",
        "cross_margin",
        "isolated_margin",
        "usd_m_futures",
        "coin_m_futures",
        "funding",
    ]


def test_configured_paper_launch_accounts_resolve_distinct_workspace_accounts(tmp_path) -> None:
    config_path = _write_paper_project(tmp_path)
    _write_account("okx-main", venue="okx", cash=500, currency="USDT", index=1, fee_rate="0.002")
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            '[account]\nref = "main"\n',
            "\n".join(
                [
                    "[accounts.account1]",
                    'ref = "main"',
                    'books = ["spot"]',
                    "",
                    "[accounts.account2]",
                    'ref = "okx-main"',
                    "index = 1",
                    'books = ["swap"]',
                    "",
                ]
            ),
        ),
        encoding="utf-8",
    )

    configured = configured_paper(config_path, market_feed_resolver_builder=_paper_feed_resolver_builder(), account_resolver=_resolver(config_path))
    result = TradingSystemLauncher().launch_configured_paper(configured)
    books = result.views.require("account.books").books

    assert configured.launch_account_configs["account1"].account_id == "main"
    assert configured.launch_account_configs["account2"].account_id == "okx-main"
    assert {(book.account_alias, book.broker, book.account_id, book.book_kind) for book in books} == {
        ("account1", "paper", "main", "spot"),
        ("account2", "okx", "okx-main", "swap"),
    }
    assert {(str(fee.book.account_id), str(fee.book.book), fee.taker) for fee in result.views.require("account.fees").fees} == {
        ("main", "spot", Decimal("0")),
        ("okx-main", "swap", Decimal("0.002")),
    }


def test_configured_paper_can_stream_market_data_from_integration_feed(tmp_path) -> None:
    config_path = _write_streaming_paper_project(tmp_path)

    result = TradingSystemLauncher().launch_configured_paper(
        configured_paper(config_path, market_feed_factory=lambda venue: FakePaperFeed(), account_resolver=_resolver(config_path))
    )

    assert result.mode.value == "paper"
    assert result.runtime.event_count == 2
    assert len(result.fills) == 1
    assert result.account_view.cash == result.final_equity
    launch_directory = tmp_path / ".kairos" / "launches" / "paper" / "paper-streaming-1"
    assert json.loads((launch_directory / "summary.json").read_text(encoding="utf-8"))["launch_id"] == "paper-streaming-1"
    assert (launch_directory / "account" / "current.json").exists()


def test_configured_paper_uses_strategy_subscription_for_feed(tmp_path) -> None:
    config_path = _write_market_section_paper_project(tmp_path)

    configured = configured_paper(config_path, market_feed_factory=lambda venue: FakePaperFeed(), account_resolver=_resolver(config_path))
    result = TradingSystemLauncher().launch_configured_paper(configured)

    assert result.mode.value == "paper"
    assert result.runtime.event_count == 2
    assert len(result.fills) == 1
    assert configured.normalized_config["paper"] == {}
    assert configured.normalized_config["market"] == {"venue": "binance", "market": "spot"}


def test_configured_paper_selects_account_id_for_streaming_feed(tmp_path) -> None:
    config_path = _write_streaming_paper_project(tmp_path, extra_accounts=True, account_id="alt", alt_fee_rate="0.001")

    configured = configured_paper(config_path, market_feed_factory=lambda venue: FakePaperFeed(), account_resolver=_resolver(config_path))
    result = TradingSystemLauncher().launch_configured_paper(configured)

    assert result.account.book.account_id == "alt"
    assert configured.normalized_config["account"]["cash"] == 500
    assert configured.normalized_config["account"]["fee_rate"] == Decimal("0.001")


def test_configured_paper_applies_account_fee_rate(tmp_path) -> None:
    config_path = _write_paper_project(tmp_path, fee_rate="0.001")

    result = TradingSystemLauncher().launch_configured_paper(configured_paper(config_path, account_resolver=_resolver(config_path)))

    assert result.fills[0].fee == Decimal("0.101")
    assert result.account_view.cash == Decimal("898.899")
    fees = result.views.require("account.fees")
    assert fees.total_count == 1
    assert fees.fees[0].maker == Decimal("0.001")
    assert fees.fees[0].taker == Decimal("0.001")


def test_configured_paper_can_use_exchange_venue_paper_account_for_event_source(tmp_path) -> None:
    config_path = _write_paper_project(tmp_path, account_venue="binance")

    result = TradingSystemLauncher().launch_configured_paper(configured_paper(config_path, account_resolver=_resolver(config_path)))

    assert str(result.account.book.broker) == "binance"
    assert result.mode.value == "paper"


def test_configured_paper_rejects_live_account_ref(tmp_path) -> None:
    config_path = _write_streaming_paper_project(tmp_path, account_environment="live")

    with pytest.raises(PaperConfigurationError, match="paper launches require a simulated account"):
        configured_paper(config_path, market_feed_factory=lambda venue: FakePaperFeed(), account_resolver=_resolver(config_path))


def test_configured_paper_supports_hyperliquid_default_market_feed(tmp_path) -> None:
    config_path = _write_streaming_paper_project(
        tmp_path,
        venue="hyperliquid",
        market="swap",
        symbol="BTC/USDC:USDC",
        quote_currency="USDC",
    )
    config_path.write_text(config_path.read_text(encoding="utf-8") + "\n[feeds.hyperliquid]\n", encoding="utf-8")

    configured = configured_paper(config_path, market_feed_resolver_builder=_paper_feed_resolver_builder(), account_resolver=_resolver(config_path))

    assert configured.market_data.feed_resolver is not None
    spec = MarketDataSubscriptionSpec(MarketRef.ephemeral(venue="hyperliquid", market="swap", source_symbol="BTC/USDC:USDC"), (Quote,))
    assert isinstance(configured.market_data.feed_resolver(spec).feed, HyperliquidMarketDataConnector)
    assert "market" not in configured.normalized_config


def test_configured_paper_routes_binance_equity_quotes_to_equity_feed(tmp_path) -> None:
    config_path = _write_streaming_paper_project(
        tmp_path,
        venue="binance",
        market="equity",
        symbol="AAPL",
        quote_currency="USDC",
    )
    credential_root = tmp_path / ".kairos" / "credentials"
    credential_root.mkdir(parents=True, exist_ok=True)
    (credential_root / "binance_read.toml").write_text(
        "[credential]\nbroker = \"binance\"\napi_key = \"paper-key\"\n",
        encoding="utf-8",
    )
    config_path.write_text(config_path.read_text(encoding="utf-8") + "\n[feeds.binance]\ncredential = \"binance_read\"\n", encoding="utf-8")

    configured = configured_paper(config_path, market_feed_resolver_builder=_paper_feed_resolver_builder(), account_resolver=_resolver(config_path))

    assert configured.market_data.feed_resolver is not None
    spec = MarketDataSubscriptionSpec(MarketRef.ephemeral(venue="binance", market="equity", source_symbol="AAPL"), (Quote,))
    feed = configured.market_data.feed_resolver(spec).feed
    assert isinstance(feed, BinanceEquityMarketDataConnector)
    assert feed.client.api_key == "paper-key"
    assert "market" not in configured.normalized_config


def test_configured_paper_requires_feed_for_strategy_subscription(tmp_path) -> None:
    config_path = _write_streaming_paper_project(
        tmp_path,
        venue="binance",
        market="equity",
        symbol="AAPL",
        quote_currency="USDC",
    )

    configured = configured_paper(config_path, market_feed_resolver_builder=_paper_feed_resolver_builder(), account_resolver=_resolver(config_path))
    spec = MarketDataSubscriptionSpec(MarketRef.ephemeral(venue="binance", market="equity", source_symbol="AAPL"), (Quote,))

    assert configured.market_data.feed_resolver is not None
    with pytest.raises(PaperConfigurationError, match="no configured feed"):
        configured.market_data.feed_resolver(spec)


def test_configured_paper_rejects_unknown_feed_credential(tmp_path) -> None:
    config_path = _write_streaming_paper_project(
        tmp_path,
        venue="binance",
        market="equity",
        symbol="AAPL",
        quote_currency="USDC",
    )
    config_path.write_text(config_path.read_text(encoding="utf-8") + "\n[feeds.binance]\ncredential = \"missing_read\"\n", encoding="utf-8")

    configured = configured_paper(config_path, market_feed_resolver_builder=_paper_feed_resolver_builder(), account_resolver=_resolver(config_path))
    spec = MarketDataSubscriptionSpec(MarketRef.ephemeral(venue="binance", market="equity", source_symbol="AAPL"), (Quote,))

    assert configured.market_data.feed_resolver is not None
    with pytest.raises(PaperConfigurationError, match="unknown credential"):
        configured.market_data.feed_resolver(spec)


def test_configured_paper_accepts_broker_named_paper_equity_account(tmp_path) -> None:
    _write_account("binance_paper_equity", venue="binance", cash=100000, currency="USDC")
    account_path = Path.cwd() / ".kairos" / "accounts" / "binance_paper_equity.toml"
    account_path.write_text(account_path.read_text(encoding="utf-8") + 'market = "equity"\n', encoding="utf-8")
    config_path = tmp_path / "binance_equity_aapl_paper.toml"
    (tmp_path / "strategy_mod.py").write_text(
        "\n".join(
            [
                "from kairospy.application.usecases.strategy.protocol import StrategyBase",
                "from kairospy.core.market import Quote",
                "class PaperStrategy(StrategyBase):",
                "    strategy_id = 'paper-equity'",
                "    def on_start(self, context):",
                "        context.subscribe('AAPL', exchange='binance', market_type='equity', selectors=(Quote,), identity=self.strategy_id)",
            ]
        ),
        encoding="utf-8",
    )
    config_path.write_text(
        "\n".join(
            [
                "[launch]",
                'id = "binance-equity-aapl-paper"',
                'mode = "paper"',
                'strategy = "strategy_mod:PaperStrategy"',
                "",
                "[account]",
                'ref = "binance_paper_equity"',
                "",
                "[execution]",
                'price_field = "ask"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    configured = configured_paper(config_path, account_resolver=_resolver(config_path))

    assert configured.account_config.account_id == "binance_paper_equity"
    assert configured.account_config.venue == "binance"
    assert configured.account_config.currency == "USDC"
    assert "market" not in configured.normalized_config


def test_configured_paper_can_launch_hyperliquid_streaming_feed(tmp_path) -> None:
    config_path = _write_streaming_paper_project(
        tmp_path,
        venue="hyperliquid",
        market="swap",
        symbol="BTC/USDC:USDC",
        quote_currency="USDC",
    )

    configured = configured_paper(config_path, market_feed_factory=lambda venue: FakePaperFeed(), account_resolver=_resolver(config_path))
    result = TradingSystemLauncher().launch_configured_paper(configured)

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

    result = TradingSystemLauncher().launch_configured_paper(
        configured_paper(config_path, market_feed_factory=lambda venue: FakeTimestamplessFeed(), account_resolver=_resolver(config_path))
    )

    assert result.runtime.event_count == 2
    assert result.runtime.last_event.time.tzinfo is not None


class FakePaperFeed:
    async def watch_ticker(self, symbol: str, *, params: Mapping[str, object] | None = None) -> AsyncIterator[Mapping[str, object]]:
        yield {"timestamp": 1767225600000, "bid": "100", "ask": "101"}

    async def watch_ticker_updates(self, symbol: str, *, market: MarketRef, params: Mapping[str, object] | None = None) -> AsyncIterator[object]:
        async for row in self.watch_ticker(symbol, params=params):
            yield ccxt_ticker_update(row, market=market)

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


def _write_paper_project(root: Path, *, launches_root: bool = True, fee_rate: str | None = None, account_venue: str = "paper") -> Path:
    market_id = "market:binance:spot:btc_usdt"
    instrument_id = "instrument:spot:btc:usdt"
    (root / "strategy_mod.py").write_text(
        "\n".join([
            "from decimal import Decimal",
            "from kairospy.application.usecases.strategy.protocol import StrategyBase",
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
    _write_account("main", venue=account_venue, cash=1000, currency="USDT", fee_rate=fee_rate)
    lines = [
            "[launch]",
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
    if launches_root:
        lines.append('launches_root = ".kairos/launches"')
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
    alt_fee_rate: str | None = None,
    account_environment: str = "paper",
) -> Path:
    normalized_symbol = symbol.replace("/", "_").replace(":", "_").lower()
    market_id = f"market:{venue}:{market}:{normalized_symbol}"
    instrument_id = f"instrument:{market}:btc:{quote_currency.lower()}"
    selected_account = account_id or "main"
    _write_account("main", venue=venue, cash=1000, currency=quote_currency, environment=account_environment)
    if extra_accounts:
        _write_account("alt", venue=venue, cash=500, currency=quote_currency, index=1, fee_rate=alt_fee_rate)
    (root / "strategy_mod.py").write_text(
        "\n".join([
            "from decimal import Decimal",
            "from kairospy.application.usecases.strategy.protocol import StrategyBase",
            "from kairospy.core.intent import target_position_intent",
            "from kairospy.core.market import Quote",
            "class PaperStrategy(StrategyBase):",
            "    strategy_id = 'paper-strategy'",
            "    def __init__(self, instrument_id, market_id, venue, market, symbol):",
            "        self.instrument_id = instrument_id",
            "        self.market_id = market_id",
            "        self.venue = venue",
            "        self.market = market",
            "        self.symbol = symbol",
            "        self.entered = False",
            "    def on_start(self, context):",
            "        context.subscribe(self.symbol, exchange=self.venue, market_type=self.market, selectors=(Quote,), identity=self.strategy_id)",
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
            "[launch]",
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
            f'venue = "{venue}"',
            f'market = "{market}"',
            f'symbol = "{symbol}"',
    ]
    lines.extend([
            "",
            "[paper]",
            'price_field = "ask"',
            'launches_root = ".kairos/launches"',
    ])
    config_path.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    return config_path


def _write_market_section_paper_project(root: Path) -> Path:
    market_id = "market:binance:spot:btc_usdt"
    instrument_id = "instrument:spot:btc:usdt"
    _write_account("paper", venue="binance", cash=1000, currency="USDT")
    (root / "strategy_mod.py").write_text(
        "\n".join([
            "from decimal import Decimal",
            "from kairospy.application.usecases.strategy.protocol import StrategyBase",
            "from kairospy.core.intent import target_position_intent",
            "from kairospy.core.market import Quote",
            "class PaperStrategy(StrategyBase):",
            "    strategy_id = 'paper-strategy'",
            "    def on_start(self, context):",
            "        context.subscribe('BTC/USDT', exchange='binance', market_type='spot', selectors=(Quote,))",
            "    def on_data(self, context, signal):",
            "        context.intent(target_position_intent(",
            "            strategy_id=self.strategy_id,",
            f"            instrument_id='{instrument_id}',",
            f"            market_id='{market_id}',",
            "            target_quantity=Decimal('1'),",
            "            at=signal.time,",
            "            intent_id='paper-intent',",
            "        ))",
        ]),
        encoding="utf-8",
    )
    config_path = root / "paper-market-section.toml"
    config_path.write_text(
        "\n".join([
            "[launch]",
            'id = "paper-market-section"',
            'mode = "paper"',
            'strategy = "strategy_mod:PaperStrategy"',
            "",
            "[account]",
            'ref = "paper"',
            "",
            "[market]",
            'venue = "binance"',
            'market = "spot"',
            "",
            "[execution]",
            'price_field = "ask"',
        ])
        + "\n",
        encoding="utf-8",
    )
    return config_path


def _write_account(
    account_id: str,
    *,
    venue: str,
    cash: int,
    currency: str,
    index: int = 0,
    fee_rate: str | None = None,
    environment: str = "paper",
) -> None:
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
                f'environment = "{environment}"',
                f"cash = {cash}",
                f'currency = "{currency}"',
                *([] if fee_rate is None else [f'fee_rate = "{fee_rate}"']),
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _resolver(config_path: Path):
    return TradingSystemLauncher()._account_resolver(config_path)


def _paper_feed_resolver_builder():
    def build(feeds):
        def resolve(spec):
            return configured_market_feed_for_subscription(
                spec,
                feeds=feeds,
                mode_label="paper",
                error_type=PaperConfigurationError,
            )

        return resolve

    return build
