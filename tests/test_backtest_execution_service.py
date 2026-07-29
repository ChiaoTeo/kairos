from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from kairospy.application.runtime.orchestration.kernel import RuntimeKernel
from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.application.service.domain.account import SimulatedAccount
from kairospy.application.service.domain.market import MarketDataResolver
from kairospy.application.service.modes.backtest import BacktestAccountService, BacktestExecutionService, BacktestMarketDataService
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.intent import IntentStatus, target_position_intent
from kairospy.core.market import MarketEvent, MarketSubject, Quote
from kairospy.core.reference import MarketResolver
from kairospy.infrastructure.data import DataStore


class TargetPositionStrategy:
    strategy_id = "s"

    def __init__(self, *, instrument_id: str, market_id: str) -> None:
        self.instrument_id = instrument_id
        self.market_id = market_id

    def on_start(self, context: object) -> None:
        return None

    def on_data(self, context: object, signal: RuntimeEnvelope) -> None:
        context.intent(  # type: ignore[attr-defined]
            target_position_intent(
                strategy_id=self.strategy_id,
                instrument_id=self.instrument_id,
                market_id=self.market_id,
                target_quantity=Decimal("2"),
                at=signal.time,
                intent_id="intent-1",
            )
        )

    def on_intent(self, context: object, intent: object) -> None:
        return None

    def on_clock(self, context: object, signal: object) -> None:
        return None

    def on_system(self, context: object, signal: object) -> None:
        return None

    def on_end(self, context: object) -> None:
        return None


def test_backtest_execution_service_fills_target_position_from_market_fields(tmp_path) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    market = MarketResolver(default_venue="binance", default_market="spot").resolve("BTC/USDT")
    account_config = SimulatedAccount("main", Decimal("1000"), cash_currency="USDT", broker="backtest")
    account = account_config.context
    coordinator = ExecutionCoordinator()
    account_service = BacktestAccountService(account_config, coordinator)
    data = BacktestMarketDataService(
        DataStore(tmp_path, storage_format="jsonl"),
        resolver=MarketDataResolver(MarketResolver(default_venue="binance", default_market="spot")),
    )
    execution = BacktestExecutionService(
        coordinator,
        account=account,
        cash_currency="USDT",
        price_field="ask",
    )
    kernel = RuntimeKernel(
        TargetPositionStrategy(instrument_id=market.instrument_id, market_id=market.market_id),
        data=data,
        account=account_service,
        execution=coordinator,
        providers=(execution,),
    )
    session = kernel.start()
    assert kernel.views.require("account.current.backtest.backtest.main").cash == Decimal("1000")
    event = RuntimeEnvelope(
        "market",
        "quote",
        now,
        1,
        MarketEvent(
            MarketSubject("instrument", market.instrument_id),
            now,
            Quote(
                instrument_id=market.instrument_id,
                market_id=market.market_id,
                market_key=market.market_key,
                time=now,
                bid=Decimal("100"),
                ask=Decimal("101"),
                source="binance",
            ),
            source="binance",
            available_at=now,
            sequence=1,
        ),
    )

    session.process(event)

    assert kernel.intents.get("intent-1").status is IntentStatus.SATISFIED
    assert coordinator.ledger.positions(account.account)[market.instrument_id] == Decimal("2")
    assert coordinator.ledger.cash(account.account)["USDT"] == Decimal("798")
    assert execution.fills[0].price == Decimal("101")
    assert kernel.views.require("execution.current").total_orders == 1
    assert kernel.views.require("order.current").state.latest_order.status == "filled"
    account_view = kernel.views.require("account.current.backtest.backtest.main")
    assert account_view.cash == Decimal("798")
    assert account_view.positions[0].quantity == Decimal("2")
    assert account_view.net_profit == Decimal("-202")
