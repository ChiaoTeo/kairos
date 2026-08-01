from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from kairospy.application.support.runtime.orchestration.kernel import RuntimeKernel
from kairospy.application.support.runtime.components import RuntimeComponents
from kairospy.application.support.runtime.orchestration.state import RuntimeStores
from kairospy.application.support.runtime.events import RuntimeEnvelope
from kairospy.application.usecases.account import SimulatedAccount
from kairospy.application.usecases.market import MarketDataResolver
from kairospy.application.support.runtime.services.market.modes.backtest import BacktestMarketDataService
from kairospy.application.support.runtime.services.account.modes.backtest import BacktestAccountService
from kairospy.application.support.runtime.services.execution.modes.backtest import BacktestExecutionService
from kairospy.application.support.runtime.services.application import RuntimeApplicationServices, RuntimeServiceDependencies
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.intent import IntentJournal, IntentStatus, target_position_intent
from kairospy.core.market import MarketEvent, MarketSubject, Quote, RateObservation
from kairospy.core.reference import MarketResolver
from kairospy.infrastructure.persistence.market_data.catalog import DataStore


class TargetPositionStrategy:
    strategy_id = "s"

    def __init__(self, *, instrument_id: str, market_id: str, target_quantity: Decimal = Decimal("2")) -> None:
        self.instrument_id = instrument_id
        self.market_id = market_id
        self.target_quantity = target_quantity
        self.emitted = False

    def on_start(self, context: object) -> None:
        return None

    def on_data(self, context: object, signal: RuntimeEnvelope) -> None:
        if self.emitted or signal.kind != "quote":
            return None
        self.emitted = True
        context.intent(  # type: ignore[attr-defined]
            target_position_intent(
                strategy_id=self.strategy_id,
                instrument_id=self.instrument_id,
                market_id=self.market_id,
                target_quantity=self.target_quantity,
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


def test_backtest_execution_service_fills_target_position_from_market_quote(tmp_path) -> None:
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
    intents = IntentJournal()
    kernel = RuntimeKernel(
        TargetPositionStrategy(instrument_id=market.instrument_id, market_id=market.market_id),
        components=RuntimeComponents(market=data, account=account_service, execution=execution),
        stores=RuntimeStores(intents=intents),
        services=RuntimeApplicationServices.from_dependencies(
            RuntimeServiceDependencies(
                intents=intents,
                data=data,
                    account_snapshot_store=account_service,
                    account=account_service,
                    account_catalog=account_service,
                trading_execution=execution,
                execution_coordinator=coordinator,
                fills_source=execution,
            )
        ),
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
    assert coordinator.ledger.positions(account.book)[market.instrument_id] == Decimal("2")
    assert coordinator.ledger.cash(account.book)["USDT"] == Decimal("798")
    assert execution.fills[0].price == Decimal("101")
    assert kernel.views.require("execution.current").total_orders == 1
    assert kernel.views.require("order.current").state.latest_order.status == "filled"
    account_view = kernel.views.require("account.current.backtest.backtest.main")
    assert account_view.cash == Decimal("798")
    assert account_view.positions[0].quantity == Decimal("2")
    assert account_view.net_profit == Decimal("-202")


def test_backtest_execution_service_supports_short_target_and_funding_cashflow(tmp_path) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    market = MarketResolver(default_venue="binance", default_market="swap").resolve("BTC/USDT")
    account_config = SimulatedAccount("main", Decimal("1000"), cash_currency="USDT", broker="backtest")
    account = account_config.context
    coordinator = ExecutionCoordinator()
    account_service = BacktestAccountService(account_config, coordinator)
    data = BacktestMarketDataService(
        DataStore(tmp_path, storage_format="jsonl"),
        resolver=MarketDataResolver(MarketResolver(default_venue="binance", default_market="swap")),
    )
    execution = BacktestExecutionService(
        coordinator,
        account=account,
        cash_currency="USDT",
        price_field="bid",
    )
    intents = IntentJournal()
    kernel = RuntimeKernel(
        TargetPositionStrategy(instrument_id=market.instrument_id, market_id=market.market_id, target_quantity=Decimal("-2")),
        components=RuntimeComponents(market=data, account=account_service, execution=execution),
        stores=RuntimeStores(intents=intents),
        services=RuntimeApplicationServices.from_dependencies(
            RuntimeServiceDependencies(
                intents=intents,
                data=data,
                    account_snapshot_store=account_service,
                    account=account_service,
                    account_catalog=account_service,
                trading_execution=execution,
                execution_coordinator=coordinator,
                fills_source=execution,
            )
        ),
    )
    session = kernel.start()
    quote_event = RuntimeEnvelope(
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
    funding_event = RuntimeEnvelope(
        "market",
        "funding_rate",
        datetime(2026, 1, 1, 8, tzinfo=timezone.utc),
        2,
        MarketEvent(
            MarketSubject("market", market.market_id),
            datetime(2026, 1, 1, 8, tzinfo=timezone.utc),
            RateObservation(
                rate_id=str(market.market_id),
                time=datetime(2026, 1, 1, 8, tzinfo=timezone.utc),
                rate=Decimal("0.01"),
                source="binance",
                basis="funding_rate",
                market_id=market.market_id,
                instrument_id=market.instrument_id,
                mark_price=Decimal("100"),
            ),
            source="binance",
            available_at=datetime(2026, 1, 1, 8, tzinfo=timezone.utc),
            sequence=2,
        ),
    )

    session.process(quote_event)
    session.process(funding_event)

    assert kernel.intents.get("intent-1").status is IntentStatus.SATISFIED
    assert coordinator.ledger.positions(account.book)[market.instrument_id] == Decimal("-2")
    assert coordinator.ledger.cash(account.book)["USDT"] == Decimal("1202.00")
    assert execution.fills[0].side.value == "sell"
    account_view = kernel.views.require("account.current.backtest.backtest.main")
    assert account_view.cash == Decimal("1202.00")
