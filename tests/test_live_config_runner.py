from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import pytest

from kairospy.application.service.modes.live import configured_live
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


def test_configured_live_runs_with_injected_integrations(tmp_path) -> None:
    config_path = _write_live_project(tmp_path)

    configured = configured_live(
        config_path,
        market_feed_factory=lambda venue: FakeLiveFeed(),
        broker_factory=lambda venue, credential: FakeBroker(),
    )
    result = TradingSystemLauncher().run_configured_live(configured)

    assert result.mode.value == "live"
    assert result.runtime.event_count == 2
    assert result.runtime.strategy_id == "live-strategy"
    assert configured.market_data.view().subscription_count == 1
    assert (tmp_path / ".kairos" / "runs" / "live" / "live-1" / "live_state.json").exists()
    assert (tmp_path / ".kairos" / "runs" / "live" / "live-1" / "account" / "current.json").exists()
    assert (tmp_path / ".kairos" / "runs" / "live" / "live-1" / "account" / "equity.jsonl").read_text(encoding="utf-8").strip()


def test_configured_live_restores_runtime_state(tmp_path) -> None:
    config_path = _write_live_project(tmp_path)
    configured = configured_live(
        config_path,
        market_feed_factory=lambda venue: FakeLiveFeed(),
        broker_factory=lambda venue, credential: FakeBroker(),
    )
    TradingSystemLauncher().run_configured_live(configured)

    restored = configured_live(
        config_path,
        market_feed_factory=lambda venue: FakeLiveFeed(),
        broker_factory=lambda venue, credential: FakeBroker(),
    )
    restored._restore_state()

    assert restored.coordinator.orders.get("external:binance:main:spot:venue-open-1").venue_order_id == "venue-open-1"


def test_configured_live_selects_account_index_when_venue_has_multiple_accounts(tmp_path) -> None:
    config_path = _write_live_project(tmp_path, extra_accounts=True, account_index=1)

    configured = configured_live(
        config_path,
        market_feed_factory=lambda venue: FakeLiveFeed(),
        broker_factory=lambda venue, credential: FakeBroker(),
    )
    result = TradingSystemLauncher().run_configured_live(configured)

    assert result.account.account.account_id == "alt"
    assert configured.normalized_config["account"]["account_id"] == "alt"


def _write_live_project(root: Path, *, extra_accounts: bool = False, account_index: int | None = None) -> Path:
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
        "\n".join(_live_config_lines(extra_accounts=extra_accounts, account_index=account_index))
        + "\n",
        encoding="utf-8",
    )
    return config_path


def _live_config_lines(*, extra_accounts: bool, account_index: int | None) -> list[str]:
    lines = [
            "[run]",
            'id = "live-1"',
            'mode = "live"',
            'strategy = "strategy_mod:LiveStrategy"',
            "",
            "[accounts.main]",
            'venue = "binance"',
            'currency = "USDT"',
            'credential = "env:fake"',
    ]
    if extra_accounts:
        lines.extend([
            "",
            "[accounts.alt]",
            "index = 1",
            'venue = "binance"',
            'currency = "USDT"',
            'credential = "env:fake-alt"',
        ])
    lines.extend([
            "",
            "[live]",
            'venue = "binance"',
            'market = "spot"',
            'symbol = "BTC/USDT"',
            'state_path = ".kairos/runs/live/live-1/live_state.json"',
    ])
    if account_index is not None:
        lines.append(f"account_index = {account_index}")
    lines.extend([
            "",
            "[live.safety]",
            "trading_enabled = false",
    ])
    return lines
