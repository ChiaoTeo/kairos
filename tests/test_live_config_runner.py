from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
import json
from pathlib import Path

import pytest

from kairospy.application.service.modes.live import LiveConfigurationError, configured_live
from kairospy.application.system import TradingSystemLauncher


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)


class FakeLiveFeed:
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


class FakeBroker:
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


def test_configured_live_runs_with_injected_integrations(tmp_path) -> None:
    config_path = _write_live_project(tmp_path)

    configured = configured_live(
        config_path,
        market_feed_factory=lambda venue: FakeLiveFeed(),
        broker_factory=lambda venue, credential: FakeBroker(),
        account_resolver=_resolver(config_path),
    )
    result = TradingSystemLauncher().run_configured_live(configured)

    assert result.mode.value == "live"
    assert result.runtime.event_count == 2
    assert result.runtime.strategy_id == "live-strategy"
    assert configured.market_data.view().subscription_count == 1
    assert (tmp_path / ".kairos" / "runs" / "live" / "live-1" / "live_state.json").exists()
    assert (tmp_path / ".kairos" / "runs" / "live" / "live-1" / "account" / "current.json").exists()
    assert not (tmp_path / ".kairos" / "runs" / "live" / "live-1" / "account" / "equity.jsonl").exists()


def test_configured_live_restores_runtime_state(tmp_path) -> None:
    config_path = _write_live_project(tmp_path)
    configured = configured_live(
        config_path,
        market_feed_factory=lambda venue: FakeLiveFeed(),
        broker_factory=lambda venue, credential: FakeBroker(),
        account_resolver=_resolver(config_path),
    )
    TradingSystemLauncher().run_configured_live(configured)

    restored = configured_live(
        config_path,
        market_feed_factory=lambda venue: FakeLiveFeed(),
        broker_factory=lambda venue, credential: FakeBrokerWithoutOpenOrders(),
        account_resolver=_resolver(config_path),
    )
    TradingSystemLauncher().run_configured_live(restored)

    state = json.loads((tmp_path / ".kairos" / "runs" / "live" / "live-1" / "live_state.json").read_text(encoding="utf-8"))
    orders = state["execution"]["orders"]
    assert any(order["order_venue_id"] == "venue-open-1" for order in orders)


def test_configured_live_selects_account_ref(tmp_path) -> None:
    config_path = _write_live_project(tmp_path, account_ref="alt")

    configured = configured_live(
        config_path,
        market_feed_factory=lambda venue: FakeLiveFeed(),
        broker_factory=lambda venue, credential: FakeBroker(),
        account_resolver=_resolver(config_path),
    )
    result = TradingSystemLauncher().run_configured_live(configured)

    assert result.account.account.account_id == "alt"
    assert configured.normalized_config["account"]["account_id"] == "alt"


def test_configured_live_rejects_paper_account_ref(tmp_path) -> None:
    config_path = _write_live_project(tmp_path, account_environment="paper")

    with pytest.raises(LiveConfigurationError, match="live runs require a live account"):
        configured_live(
            config_path,
            market_feed_factory=lambda venue: FakeLiveFeed(),
            broker_factory=lambda venue, credential: FakeBroker(),
            account_resolver=_resolver(config_path),
        )


def _write_live_project(root: Path, *, account_ref: str = "main", account_environment: str = "live") -> Path:
    _write_account("main", credential="env:fake", environment=account_environment)
    if account_ref != "main":
        _write_account(account_ref, credential="env:fake-alt", index=1)
    (root / "strategy_mod.py").write_text(
        "\n".join([
            "from kairospy.application.strategy import StrategyBase",
            "class LiveStrategy(StrategyBase):",
            "    strategy_id = 'live-strategy'",
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
            "[run]",
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
            'venue = "binance"',
            'market = "spot"',
            'symbol = "BTC/USDT"',
            'state_path = ".kairos/runs/live/live-1/live_state.json"',
    ])
    lines.extend([
            "",
            "[live.safety]",
            "trading_enabled = false",
    ])
    return lines


def _write_account(account_id: str, *, credential: str, index: int = 0, environment: str = "live") -> None:
    account_root = Path.cwd() / ".kairos" / "accounts"
    account_root.mkdir(parents=True, exist_ok=True)
    (account_root / f"{account_id}.toml").write_text(
        "\n".join(
            [
                "[account]",
                f'id = "{account_id}"',
                f"index = {index}",
                'provider = "binance"',
                f'environment = "{environment}"',
                'venue = "binance"',
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
