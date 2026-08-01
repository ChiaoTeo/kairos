from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from kairospy.application.runtime.orchestration.kernel import RuntimeKernel
from kairospy.application.runtime.orchestration.state import RuntimePorts, RuntimeStores
from kairospy.application.protocol import RuntimeEnvelope, RuntimeLine
from kairospy.application.service.domain.account import SimulatedAccount, account_baseline_snapshot
from kairospy.application.service.domain.execution import JsonExecutionStateStore
from kairospy.application.service.modes.paper import PaperAccountService
from kairospy.application.service.runtime import RuntimeApplicationServices, RuntimeServiceDependencies
from kairospy.application.service.runtime.execution import ApplyExecutionUpdateUseCase
from kairospy.core.account import AccountBookKind, AccountContext, AccountBookRef, AccountSource, Environment
from kairospy.core.execution import ExecutionCoordinator, ExecutionUpdate
from kairospy.core.intent import IntentEvent, IntentEventKind, IntentJournal, IntentStatus, target_position_intent
from kairospy.core.order import OrderEventKind, OrderRequest, OrderSide, OrderType


class FakeBroker:
    def __init__(self) -> None:
        self.created: list[tuple[str, object]] = []

    def create_order(self, symbol: str, *, side: str, type: str, amount: object, price: object | None = None, params=None):
        self.created.append((symbol, params))
        return {"id": "venue-order-1"}

    def cancel_order(self, id: str, *, symbol: str | None = None, params=None):
        return {"id": id, "status": "canceled"}


class NoopStrategy:
    strategy_id = "account-domain-strategy"

    def on_start(self, context: object) -> None:
        return None

    def on_data(self, context: object, signal: object) -> None:
        return None

    def on_intent(self, context: object, intent: object) -> None:
        return None

    def on_clock(self, context: object, signal: object) -> None:
        return None

    def on_system(self, context: object, signal: object) -> None:
        return None

    def on_end(self, context: object) -> None:
        return None


def test_account_baseline_event_updates_account_view_state() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    account = SimulatedAccount("paper-main", Decimal("0"), cash_currency="USDT", broker="paper", environment=Environment.PAPER)
    coordinator = ExecutionCoordinator()
    account_service = PaperAccountService(account, coordinator)
    snapshot = account_baseline_snapshot(
        account.context,
        at=now,
        currency="USDT",
        equity=Decimal("1000"),
        source=AccountSource.SIMULATED,
    )
    event = RuntimeEnvelope("account", "baseline", now, 1, snapshot)
    intents = IntentJournal()
    kernel = RuntimeKernel(
        NoopStrategy(),
        ports=RuntimePorts(account=account_service),
        stores=RuntimeStores(intents=intents),
        services=RuntimeApplicationServices.from_dependencies(
            RuntimeServiceDependencies(
                intents=intents,
                account_snapshot_store=account_service,
                account=account_service,
                execution=coordinator,
            )
        ),
    )

    asyncio.run(kernel.run(RuntimeLine((event,))))

    view = kernel.views.require("account.current.paper.paper.paper_main")
    assert view.event_count == 1
    assert view.cash == Decimal("1000")
    assert view.initial_equity == Decimal("1000")


def test_json_execution_state_store_round_trips_snapshot(tmp_path) -> None:
    coordinator = ExecutionCoordinator()
    store = JsonExecutionStateStore(tmp_path / "execution.json")

    saved = store.save(coordinator)
    loaded = store.load()

    assert loaded == saved
    assert (tmp_path / "execution.json").exists()


def test_account_ref_models_identity_book_and_legacy_segment() -> None:
    account = AccountBookRef("binance", "main", AccountBookKind.USD_M_FUTURES)

    assert str(account.broker) == "binance"
    assert str(account.account_id) == "main"
    assert account.book is AccountBookKind.USD_M_FUTURES
    assert account.segment == "usd_m_futures"
    assert account.value == "binance:main:usd_m_futures"


def test_order_id_preserves_intent_context_and_resolves_order_venue_id() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    account = SimulatedAccount("paper-main", Decimal("0"), cash_currency="USDT", broker="paper", environment=Environment.PAPER)
    coordinator = ExecutionCoordinator(broker=FakeBroker())
    order_id = "intent:rebalance-btc:order:0001"
    request = OrderRequest(
        order_id,
        account.context,
        "instrument:spot:btc:usdt",
        OrderSide.BUY,
        Decimal("1"),
    )

    coordinator.plan_order(request, at=now)
    state = coordinator.submit_order(order_id, at=now)

    assert state.order_id == order_id
    assert state.order_venue_id == "venue-order-1"
    assert coordinator.orders.get(order_id).order_venue_id == "venue-order-1"
    assert coordinator.orders.get_by_order_venue_id("venue-order-1").order_id == order_id


def test_execution_coordinator_resolves_broker_by_order_account() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    binance = AccountBookRef("binance", "main", AccountBookKind.SPOT)
    okx = AccountBookRef("okx", "hedge", AccountBookKind.USD_M_FUTURES)
    binance_broker = FakeBroker()
    okx_broker = FakeBroker()
    coordinator = ExecutionCoordinator(
        broker_resolver=lambda account: {
            binance: binance_broker,
            okx: okx_broker,
        }.get(account),
        broker_symbol_resolver=lambda symbol: "ETH/USDT",
    )
    request = OrderRequest(
        "order-okx",
        AccountContext(okx, Environment.LIVE),
        "okx:swap:ETH/USDT",
        OrderSide.BUY,
        Decimal("1"),
    )

    coordinator.plan_order(request, at=now)
    state = coordinator.submit_order("order-okx", at=now, params={"type": "swap"})

    assert state.order_venue_id == "venue-order-1"
    assert binance_broker.created == []
    assert okx_broker.created == [("ETH/USDT", {"type": "swap"})]


def test_apply_execution_update_use_case_updates_order_ledger_and_intent() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    account = AccountContext(AccountBookRef("binance", "main", AccountBookKind.SPOT), Environment.LIVE)
    coordinator = ExecutionCoordinator(broker=FakeBroker(), broker_symbol_resolver=lambda symbol: "ETH/USDT")
    intents = IntentJournal()
    intent = target_position_intent(
        strategy_id="account-domain-strategy",
        instrument_id="instrument:spot:eth:usdt",
        market_id="market:binance:spot:eth-usdt",
        target_quantity=Decimal("1"),
        at=now,
        intent_id="intent-update-use-case",
    )
    intents.record_intent(intent, at=now)
    request = OrderRequest(
        "intent-update-use-case-order-1",
        account,
        intent.instrument_id,
        OrderSide.BUY,
        Decimal("1"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("101"),
        market_id=intent.market_id,
    )
    coordinator.plan_order(request, at=now)
    intents.record(IntentEvent(intent.intent_id, IntentEventKind.ACCEPTED, now))
    intents.record(IntentEvent(intent.intent_id, IntentEventKind.PLANNED, now, order_ids=(request.order_id,)))
    coordinator.submit_order(request.order_id, at=now)
    intents.record(IntentEvent(intent.intent_id, IntentEventKind.ORDERING, now, order_ids=(request.order_id,)))
    use_case = ApplyExecutionUpdateUseCase(coordinator, intents=intents)

    state = use_case.apply(
        ExecutionUpdate(
            observed_at=now,
            kind=OrderEventKind.PARTIALLY_FILLED,
            order_venue_id="venue-order-1",
            fill_quantity=Decimal("1"),
            fill_price=Decimal("101"),
            filled_quantity=Decimal("1"),
            settlement_currency="USDT",
        )
    )

    assert state.status.value == "filled"
    assert coordinator.orders.get(request.order_id).status.value == "filled"
    assert coordinator.ledger.cash(account.book)["USDT"] == Decimal("-101")
    assert intents.get(intent.intent_id).status is IntentStatus.SATISFIED


def test_execution_runtime_update_drives_coordinator_intent_and_views() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    account = AccountContext(AccountBookRef("binance", "main", AccountBookKind.SPOT), Environment.LIVE)
    coordinator = ExecutionCoordinator(broker=FakeBroker(), broker_symbol_resolver=lambda symbol: "ETH/USDT")
    intents = IntentJournal()
    intent = target_position_intent(
        strategy_id="account-domain-strategy",
        instrument_id="instrument:spot:eth:usdt",
        market_id="market:binance:spot:eth-usdt",
        target_quantity=Decimal("1"),
        at=now,
        intent_id="intent-fill-1",
    )
    intents.record_intent(intent, at=now)
    request = OrderRequest(
        "intent-fill-1-order-1",
        account,
        intent.instrument_id,
        OrderSide.BUY,
        Decimal("1"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("101"),
        market_id=intent.market_id,
    )
    coordinator.plan_order(request, at=now)
    intents.record(IntentEvent(intent.intent_id, IntentEventKind.ACCEPTED, now))
    intents.record(IntentEvent(intent.intent_id, IntentEventKind.PLANNED, now, order_ids=(request.order_id,)))
    coordinator.submit_order(request.order_id, at=now)
    intents.record(IntentEvent(intent.intent_id, IntentEventKind.ORDERING, now, order_ids=(request.order_id,)))
    event = RuntimeEnvelope(
        "execution",
        "trade_update",
        now,
        1,
        ExecutionUpdate(
            observed_at=now,
            kind=OrderEventKind.PARTIALLY_FILLED,
            order_venue_id="venue-order-1",
            fill_quantity=Decimal("1"),
            fill_price=Decimal("101"),
            filled_quantity=Decimal("1"),
            settlement_currency="USDT",
        ),
    )
    kernel = RuntimeKernel(
        NoopStrategy(),
        stores=RuntimeStores(intents=intents),
        services=RuntimeApplicationServices.from_dependencies(
            RuntimeServiceDependencies(intents=intents, execution=coordinator)
        ),
    )

    asyncio.run(kernel.run(RuntimeLine((event,))))

    assert coordinator.orders.get(request.order_id).status.value == "filled"
    assert coordinator.ledger.cash(account.book)["USDT"] == Decimal("-101")
    assert intents.get(intent.intent_id).status is IntentStatus.SATISFIED
    assert kernel.views.require("execution.current").latest_order.status == "filled"
    assert kernel.views.require("order.current").state.latest_order.status == "filled"
