from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys

from kairospy.core.reference import MarketRef
from kairospy.application.service.operations.run import configured_live_target


class FakeOkxExchange:
    def fetch_quote(self, market: MarketRef, *, params=None):
        assert market.venue == "okx"
        assert market.market == "spot"
        assert market.source_symbol == "BTC/USDT"
        return {
            "time": "2026-01-01T00:00:00+00:00",
            "kind": "ticker",
            "market_id": market.market_id,
            "instrument_id": market.instrument_id,
            "market_key": market.market_key,
            "venue": market.venue,
            "market": market.market,
            "source_symbol": market.source_symbol,
            "bid1": "100",
            "ask1": "101",
            "last": "100.5",
        }


class FakeOkxBroker:
    def __init__(self) -> None:
        self.created_orders = []
        self.replay_created_order_stream = False

    def fetch_balance(self, *, params=None):
        return {
            "free": {"USDT": "1000"},
            "used": {"USDT": "0"},
            "total": {"USDT": "1000"},
        }

    def fetch_open_orders(self, symbol=None, *, since=None, limit=None, params=None):
        assert symbol == "BTC/USDT"
        return ()

    def create_order(self, symbol, *, side, type, amount, price=None, params=None):
        order = {
            "id": "okx-live-1",
            "symbol": symbol,
            "side": side,
            "type": type,
            "amount": str(amount),
            "price": None if price is None else str(price),
            "params": dict(params or {}),
        }
        self.created_orders.append(order)
        return order

    async def watch_balance(self, *, params=None):
        if False:
            yield {}

    async def watch_orders(self, symbol=None, *, since=None, limit=None, params=None):
        assert symbol == "BTC/USDT"
        if self.replay_created_order_stream and self.created_orders:
            order = self.created_orders[-1]
            yield {
                "id": order["id"],
                "symbol": order["symbol"],
                "side": order["side"],
                "type": order["type"],
                "amount": order["amount"],
                "filled": "0",
                "remaining": order["amount"],
                "price": order["price"],
                "status": "open",
                "timestamp": 1767225600000,
            }

    async def watch_my_trades(self, symbol=None, *, since=None, limit=None, params=None):
        assert symbol == "BTC/USDT"
        if self.replay_created_order_stream and self.created_orders:
            order = self.created_orders[-1]
            yield {
                "id": "okx-trade-1",
                "order": order["id"],
                "symbol": order["symbol"],
                "amount": order["amount"],
                "price": order["price"],
                "cost": "10",
                "timestamp": 1767225660000,
                "fee": {"currency": "USDT", "cost": "0.01"},
            }


def test_configured_okx_live_target_defaults_to_read_only_trading(tmp_path: Path) -> None:
    config_path = _write_live_config(tmp_path, safety="")
    broker = FakeOkxBroker()

    target = configured_live_target(
        config_path,
        exchange_factory=lambda venue: FakeOkxExchange(),
        broker_factory=lambda venue: broker,
    )

    result = target.engine.run(target.source_factory(1), symbol="BTC/USDT")

    assert broker.created_orders == []
    assert [state.status.value for state in result.runtime.intent_states] == ["rejected"]


def test_configured_okx_live_target_uses_configured_max_iterations(tmp_path: Path) -> None:
    config_path = _write_live_config(tmp_path, safety="", extra_live='max_iterations = 1\n')

    target = configured_live_target(
        config_path,
        exchange_factory=lambda venue: FakeOkxExchange(),
        broker_factory=lambda venue: FakeOkxBroker(),
    )

    assert target.max_iterations == 1


def test_configured_okx_live_target_can_submit_when_safety_enables_trading(tmp_path: Path) -> None:
    config_path = _write_live_config(
        tmp_path,
        safety="""
[live.safety]
trading_enabled = true
require_limit_orders = true
max_order_notional = "20"
""",
    )
    broker = FakeOkxBroker()

    target = configured_live_target(
        config_path,
        exchange_factory=lambda venue: FakeOkxExchange(),
        broker_factory=lambda venue: broker,
    )

    result = target.engine.run(
        target.source_factory(1),
        symbol="BTC/USDT",
        order_params={**target.order_params, "timeInForce": "GTC"},
    )

    assert broker.created_orders == [
        {
            "id": "okx-live-1",
            "symbol": "BTC/USDT",
            "side": "buy",
            "type": "limit",
            "amount": "0.1",
            "price": "100",
            "params": {"type": "spot", "timeInForce": "GTC"},
        }
    ]
    assert [state.status.value for state in result.runtime.intent_states] == ["ordering"]
    assert result.account_view.pending_orders[0].venue_order_id == "okx-live-1"


def test_configured_okx_live_target_reconciles_created_order_trade_on_next_iteration(tmp_path: Path) -> None:
    config_path = _write_live_config(
        tmp_path,
        safety="""
[live.safety]
trading_enabled = true
require_limit_orders = true
max_order_notional = "20"
""",
    )
    broker = FakeOkxBroker()
    target = configured_live_target(
        config_path,
        exchange_factory=lambda venue: FakeOkxExchange(),
        broker_factory=lambda venue: broker,
    )

    first = target.engine.run(
        target.source_factory(1),
        symbol="BTC/USDT",
        order_params=target.order_params,
    )
    broker.replay_created_order_stream = True
    second = target.engine.run(
        target.source_factory(2),
        symbol="BTC/USDT",
        order_params=target.order_params,
        max_order_events=1,
        max_trade_events=1,
    )

    assert first.account_view.pending_orders[0].venue_order_id == "okx-live-1"
    assert broker.created_orders == [
        {
            "id": "okx-live-1",
            "symbol": "BTC/USDT",
            "side": "buy",
            "type": "limit",
            "amount": "0.1",
            "price": "100",
            "params": {"type": "spot"},
        }
    ]
    assert [state.status.value for state in second.coordinator.orders.states] == ["filled"]
    assert second.coordinator.ledger.cash(second.account.account) == {"USDT": Decimal("-10.01")}
    assert second.coordinator.ledger.positions(second.account.account) == {"instrument:spot:btc:usdt": Decimal("0.1")}
    assert second.account_view.pending_orders == ()


def test_example_okx_live_strategy_submits_limit_order_when_trading_is_enabled(tmp_path: Path) -> None:
    config_path = tmp_path / "okx_live_example.toml"
    config_path.write_text(
        """
[run]
id = "okx-live-example"
mode = "live"
strategy = "examples.strategies.okx:LiveLimitLong"

[live]
venue = "okx"
market = "spot"
symbol = "BTC/USDT"
equity_currency = "USDT"

[live.safety]
trading_enabled = true
require_limit_orders = true
max_order_notional = "20"

[accounts.okx_main]
index = 0
venue = "okx"
currency = "USDT"
credential = "env:okx-main"
""",
        encoding="utf-8",
    )
    broker = FakeOkxBroker()
    project_root = Path(__file__).resolve().parents[1]
    inserted = False
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
        inserted = True

    try:
        target = configured_live_target(
            config_path,
            exchange_factory=lambda venue: FakeOkxExchange(),
            broker_factory=lambda venue: broker,
        )
        result = target.engine.run(
            target.source_factory(1),
            symbol="BTC/USDT",
            order_params=target.order_params,
        )
    finally:
        if inserted:
            sys.path.remove(str(project_root))

    assert broker.created_orders == [
        {
            "id": "okx-live-1",
            "symbol": "BTC/USDT",
            "side": "buy",
            "type": "limit",
            "amount": "0.001",
            "price": "101",
            "params": {"type": "spot"},
        }
    ]
    assert [state.status.value for state in result.runtime.intent_states] == ["ordering"]


def _write_live_config(tmp_path: Path, *, safety: str, extra_live: str = "") -> Path:
    strategy_path = tmp_path / "limit_strategy.py"
    strategy_path.write_text(
        """
from decimal import Decimal

from kairospy.application.context import StrategyContext
from kairospy.application.strategy import StrategyBase


class LimitLong(StrategyBase):
    strategy_id = "okx-live-limit-long"

    def on_market(self, context: StrategyContext, event):
        context.target_position(
            "okx:spot:BTC/USDT",
            Decimal("0.1"),
            limit_price=Decimal("100"),
            account=0,
            intent_id="enter-okx",
        )
        return ()
""",
        encoding="utf-8",
    )
    config_path = tmp_path / "okx_live.toml"
    config_path.write_text(
        f"""
[run]
id = "okx-live"
mode = "live"
strategy = "limit_strategy:LimitLong"

[live]
venue = "okx"
market = "spot"
symbol = "BTC/USDT"
equity_currency = "USDT"
{extra_live}

[accounts.okx_main]
index = 0
venue = "okx"
currency = "USDT"
credential = "env:okx-main"
{safety}
""",
        encoding="utf-8",
    )
    return config_path
