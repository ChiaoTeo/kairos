from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
import json
from pathlib import Path

import pytest

from kairospy.application.support.launch.config.live import LiveConfigurationError, configured_live
from kairospy.application.support.launch.launcher import TradingSystemLauncher
from kairospy.core.account import AccountBookRef
from kairospy.core.reference import MarketRef
from kairospy.infrastructure.integrations.payloads.ccxt_market import ccxt_ticker_update


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)


class FakeLiveFeed:
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


class FakeBroker:
    def __init__(self, venue: str = "binance") -> None:
        self.venue = venue
        self.balance_calls = 0

    def create_order(
        self,
        symbol: str,
        *,
        side: str,
        type: str,
        amount: object,
        price: object | None = None,
        params: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        return {"id": "venue-order-1"}

    def cancel_order(
        self,
        id: str,
        *,
        symbol: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        return {"id": id, "status": "canceled"}

    def fetch_balance(self, *, params: Mapping[str, object] | None = None) -> Mapping[str, object]:
        self.balance_calls += 1
        return {"free": {"USDT": "1000"}, "used": {"USDT": "0"}, "total": {"USDT": "1000"}}

    def fetch_open_orders(
        self,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> tuple[Mapping[str, object], ...]:
        return (
            {
                "id": "venue-open-1",
                "symbol": "BTC/USDT",
                "side": "buy",
                "type": "limit",
                "amount": "1",
                "filled": "0",
                "remaining": "1",
                "price": "100",
            },
        )


class FakeBrokerWithoutOpenOrders(FakeBroker):
    def fetch_open_orders(
        self,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> tuple[Mapping[str, object], ...]:
        return ()


def test_configured_live_launches_with_injected_integrations(tmp_path) -> None:
    config_path = _write_live_project(tmp_path)

    configured = configured_live(
        config_path,
        market_feed_factory=lambda venue: FakeLiveFeed(),
        broker_factory=lambda book, credential: FakeBroker(str(book.broker)),
        account_resolver=_resolver(config_path),
    )
    result = TradingSystemLauncher().launch_configured_live(configured)

    assert result.mode.value == "live"
    assert result.runtime.event_count == 2
    assert result.runtime.strategy_id == "live-strategy"
    assert configured.market_data.view().subscription_count == 1
    assert configured.private_sync.enabled is True
    assert not hasattr(configured, "live_config")
    assert not hasattr(configured, "venue")
    assert not hasattr(configured, "market")
    assert (tmp_path / ".kairos" / "launches" / "live" / "live-1" / "live_state.json").exists()
    assert (tmp_path / ".kairos" / "launches" / "live" / "live-1" / "account" / "current.json").exists()
    assert not (tmp_path / ".kairos" / "launches" / "live" / "live-1" / "account" / "equity.jsonl").exists()


def test_configured_live_accepts_launch_account_references(tmp_path) -> None:
    config_path = _write_live_project(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace('[account]\nref = "main"\n', '[accounts.account1]\nref = "main"\nbooks = ["spot"]\n'),
        encoding="utf-8",
    )

    configured = configured_live(
        config_path,
        market_feed_factory=lambda venue: FakeLiveFeed(),
        broker_factory=lambda book, credential: FakeBroker(str(book.broker)),
        account_resolver=_resolver(config_path),
    )

    assert configured.account_config.account_id == "main"
    assert configured.normalized_config["accounts"]["account1"]["books"] == ["spot"]


def test_configured_live_launch_accounts_use_distinct_brokers(tmp_path) -> None:
    config_path = _write_live_project(tmp_path)
    _write_account("okx-main", credential="okx", index=1, venue="okx")
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
    brokers: dict[str, FakeBroker] = {}

    def broker_factory(book: AccountBookRef, credential: str | None) -> FakeBroker:
        broker = FakeBroker(str(book.broker))
        brokers[str(book.broker)] = broker
        return broker

    configured = configured_live(
        config_path,
        market_feed_factory=lambda venue: FakeLiveFeed(),
        broker_factory=broker_factory,
        account_resolver=_resolver(config_path),
    )
    result = TradingSystemLauncher().launch_configured_live(configured)
    books = result.views.require("account.books").books

    assert set(brokers) == {"binance", "okx"}
    assert brokers["binance"].balance_calls == 1
    assert brokers["okx"].balance_calls == 1
    assert {(book.account_alias, book.broker, book.account_id, book.book_kind) for book in books} == {
        ("account1", "binance", "main", "spot"),
        ("account2", "okx", "okx-main", "swap"),
    }


def test_configured_live_broker_factory_is_account_book_aware(tmp_path) -> None:
    config_path = _write_live_project(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            '[account]\nref = "main"\n',
            "\n".join(
                [
                    "[accounts.account1]",
                    'ref = "main"',
                    'books = ["spot", "equity"]',
                    "",
                ]
            ),
        ),
        encoding="utf-8",
    )
    brokers: dict[str, FakeBroker] = {}

    def broker_factory(book: AccountBookRef, credential: str | None) -> FakeBroker:
        broker = FakeBroker(str(book.broker))
        brokers[book.value] = broker
        return broker

    configured = configured_live(
        config_path,
        market_feed_factory=lambda venue: FakeLiveFeed(),
        broker_factory=broker_factory,
        account_resolver=_resolver(config_path),
    )
    result = TradingSystemLauncher().launch_configured_live(configured)
    books = result.views.require("account.books").books

    assert set(brokers) == {"binance:main:spot", "binance:main:equity"}
    assert brokers["binance:main:spot"].balance_calls == 1
    assert brokers["binance:main:equity"].balance_calls == 1
    assert {(book.account_alias, book.broker, book.account_id, book.book_kind) for book in books} == {
        ("account1", "binance", "main", "spot"),
        ("account1", "binance", "main", "equity"),
    }


def test_configured_live_readonly_credential_disables_trading_capability(tmp_path) -> None:
    config_path = _write_live_project(tmp_path)
    account_path = tmp_path / ".kairos" / "accounts" / "main.toml"
    account_path.write_text(
        "\n".join(
            [
                "[account]",
                'id = "main"',
                "index = 0",
                'provider = "binance"',
                'environment = "live"',
                'venue = "binance"',
                'market = "spot"',
                'currency = "USDT"',
                "",
                "[credentials.readonly]",
                'ref = "binance_read"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    seen_credentials: list[str | None] = []

    def broker_factory(book: AccountBookRef, credential: str | None) -> FakeBroker:
        seen_credentials.append(credential)
        return FakeBroker(str(book.broker))

    configured = configured_live(
        config_path,
        market_feed_factory=lambda venue: FakeLiveFeed(),
        broker_factory=broker_factory,
        account_resolver=_resolver(config_path),
    )
    result = TradingSystemLauncher().launch_configured_live(configured)

    capabilities = result.views.require("account.capabilities").capabilities
    assert capabilities[0].can_trade is False
    assert seen_credentials[0] == "binance_read"


def test_configured_live_restores_runtime_state(tmp_path) -> None:
    config_path = _write_live_project(tmp_path)
    configured = configured_live(
        config_path,
        market_feed_factory=lambda venue: FakeLiveFeed(),
        broker_factory=lambda book, credential: FakeBroker(str(book.broker)),
        account_resolver=_resolver(config_path),
    )
    TradingSystemLauncher().launch_configured_live(configured)

    restored = configured_live(
        config_path,
        market_feed_factory=lambda venue: FakeLiveFeed(),
        broker_factory=lambda book, credential: FakeBrokerWithoutOpenOrders(str(book.broker)),
        account_resolver=_resolver(config_path),
    )
    TradingSystemLauncher().launch_configured_live(restored)

    state = json.loads((tmp_path / ".kairos" / "launches" / "live" / "live-1" / "live_state.json").read_text(encoding="utf-8"))
    orders = state["execution"]["orders"]
    assert any(order["order_venue_id"] == "venue-open-1" for order in orders)


def test_configured_live_selects_account_ref(tmp_path) -> None:
    config_path = _write_live_project(tmp_path, account_ref="alt")

    configured = configured_live(
        config_path,
        market_feed_factory=lambda venue: FakeLiveFeed(),
        broker_factory=lambda book, credential: FakeBroker(str(book.broker)),
        account_resolver=_resolver(config_path),
    )
    result = TradingSystemLauncher().launch_configured_live(configured)

    assert result.account.book.account_id == "alt"
    assert configured.normalized_config["account"]["account_id"] == "alt"


def test_configured_live_rejects_paper_account_ref(tmp_path) -> None:
    config_path = _write_live_project(tmp_path, account_environment="paper")

    with pytest.raises(LiveConfigurationError, match="live launches require a live account"):
        configured_live(
            config_path,
            market_feed_factory=lambda venue: FakeLiveFeed(),
            broker_factory=lambda book, credential: FakeBroker(str(book.broker)),
            account_resolver=_resolver(config_path),
        )


def _write_live_project(root: Path, *, account_ref: str = "main", account_environment: str = "live") -> Path:
    _write_account("main", credential="fake", environment=account_environment)
    if account_ref != "main":
        _write_account(account_ref, credential="fake-alt", index=1)
    (root / "strategy_mod.py").write_text(
        "\n".join([
            "from kairospy.application.usecases.strategy.protocol import StrategyBase",
            "from kairospy.core.market import Quote",
            "class LiveStrategy(StrategyBase):",
            "    strategy_id = 'live-strategy'",
            "    def on_start(self, context):",
            "        context.subscribe('BTC/USDT', exchange='binance', market_type='spot', selectors=(Quote,), identity=self.strategy_id)",
            "    def on_data(self, context, signal):",
            "        return None",
        ]),
        encoding="utf-8",
    )
    config_path = root / "live.toml"
    config_path.write_text(
        "\n".join(_live_config_lines(account_ref=account_ref))
        + "\n",
        encoding="utf-8",
    )
    return config_path


def _live_config_lines(*, account_ref: str) -> list[str]:
    lines = [
            "[launch]",
            'id = "live-1"',
            'mode = "live"',
            'strategy = "strategy_mod:LiveStrategy"',
            "",
            "[account]",
            f'ref = "{account_ref}"',
    ]
    lines.extend([
            "",
            "[live]",
            'state_path = ".kairos/launches/live/live-1/live_state.json"',
    ])
    lines.extend([
            "",
            "[live.safety]",
            "trading_enabled = false",
    ])
    return lines


def _write_account(account_id: str, *, credential: str, index: int = 0, environment: str = "live", venue: str = "binance") -> None:
    account_root = Path.cwd() / ".kairos" / "accounts"
    account_root.mkdir(parents=True, exist_ok=True)
    (account_root / f"{account_id}.toml").write_text(
        "\n".join(
            [
                "[account]",
                f'id = "{account_id}"',
                f"index = {index}",
                f'provider = "{venue}"',
                f'environment = "{environment}"',
                f'venue = "{venue}"',
                'market = "spot"',
                'currency = "USDT"',
                f'credential = "{credential}"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _resolver(config_path: Path):
    return TradingSystemLauncher()._account_resolver(config_path)
