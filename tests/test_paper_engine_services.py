from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from kairospy.application.runtime.orchestration.kernel import RuntimeKernel
from kairospy.application.runtime.protocol import RuntimeEnvelope, RuntimeLine
from kairospy.application.service.domain.account import SimulatedAccount
from kairospy.application.service.modes.paper import PaperAccountService, PaperExecutionService, PaperMarketDataService
from kairospy.core.account import Environment
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.intent import IntentStatus, target_position_intent
from kairospy.core.market import MarketEvent, MarketSubject, Quote
from kairospy.core.reference import MarketResolver


class PaperTargetPositionStrategy:
    strategy_id = "paper-strategy"

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
                target_quantity=Decimal("1"),
                at=signal.time,
                intent_id="paper-intent-1",
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


def test_paper_services_stream_market_data_and_simulate_execution() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    market = MarketResolver(default_venue="binance", default_market="spot").resolve("ETH/USDT")
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
                bid=Decimal("2000"),
                ask=Decimal("2001"),
                source="binance",
            ),
            source="binance",
            available_at=now,
            sequence=1,
        ),
    )
    account_config = SimulatedAccount(
        "paper-main",
        Decimal("5000"),
        cash_currency="USDT",
        broker="paper",
        environment=Environment.PAPER,
    )
    coordinator = ExecutionCoordinator()
    account = PaperAccountService(account_config, coordinator)
    market_data = PaperMarketDataService(RuntimeLine((event,)), source_name="paper-test-feed")
    execution = PaperExecutionService(
        coordinator,
        account=account_config.context,
        cash_currency="USDT",
        price_field="ask",
    )
    kernel = RuntimeKernel(
        PaperTargetPositionStrategy(instrument_id=market.instrument_id, market_id=market.market_id),
        data=market_data,
        account=account,
        execution=coordinator,
        providers=(execution,),
    )

    runtime_result = asyncio.run(kernel.run())

    assert runtime_result.event_count == 1
    assert kernel.intents.get("paper-intent-1").status is IntentStatus.SATISFIED
    assert execution.fills[0].price == Decimal("2001")
    assert coordinator.ledger.cash(account_config.context.account)["USDT"] == Decimal("2999")
    assert coordinator.ledger.positions(account_config.context.account)[market.instrument_id] == Decimal("1")
    account_view = kernel.views.require("account.current.paper.paper.paper_main")
    assert account_view.cash == Decimal("2999")
    assert account_view.positions[0].quantity == Decimal("1")
    assert kernel.views.require("market.service").source == "paper-test-feed"
    assert kernel.views.require("order.current").state.latest_order.status == "filled"
