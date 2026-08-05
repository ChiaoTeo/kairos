from __future__ import annotations

from dataclasses import dataclass
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from kairospy.application.usecases.account.application.accounts import AccountApplicationService
from kairospy.application.usecases.account.application.directory import AccountDirectory
from kairospy.application.usecases.account.services.runtime.projections import RuntimeAccountService, RuntimeAccountViewProjectionService
from kairospy.application.usecases.account.services.runtime.modes.paper import PaperAccountService
from kairospy.application.usecases.account.application.runtime import InitialAssetBalance
from kairospy.application.usecases.account.protocol import AccountLoginRequest, AccountLoginResult, AccountSession
from kairospy.application.usecases.execution.application.component import ExecutionApplication, ExecuteIntentCommand, PlanOrderCommand, SubmitOrderCommand
from kairospy.application.usecases.execution.application.state import ExecutionStateSnapshot
from kairospy.application.usecases.execution.application.runtime import build_execution_coordinator, execution_runtime_adapters
from kairospy.application.usecases.execution.services.runtime.projections import RuntimeExecutionService, TradingRuntimeExecutionService
from kairospy.application.usecases.execution.services.runtime.modes.paper import PaperExecutionService
from kairospy.application.usecases.execution.services.coordinator import ExecutionCoordinator
from kairospy.application.usecases.account.domain.simulated import SimulatedAccount
from kairospy.application.usecases.execution.domain.policy import ExecutionSafetyPolicy
from kairospy.application.actor.account.application.projectors import AccountActorProjectors
from kairospy.application.actor.account.application.actor import AccountActor, RecordIntentsCommand
from kairospy.application.support.runtime.application.views import ViewStore
from kairospy.application.support.messaging import Message
from kairospy.application.usecases.strategy.application.context import StrategyContext
from kairospy.application.system.application.business import SystemBusinessRuntime
from kairospy.application.usecases.account.application.queries import AccountViewQueryService
from kairospy.infrastructure.integrations.application.execution import ConnectionOrderCancelRequest, ConnectionOrderCancelResult, ConnectionOrderSubmissionRequest, ConnectionOrderSubmissionResult
from kairospy.domain.account import AccountSegment, AccountRuntimeContext, AccountEvent, AccountEventKind, Environment
from kairospy.domain.order import OrderRequest, OrderSide, OrderStatus, OrderType
from kairospy.domain.execution import ExecutionUpdate
from kairospy.domain.market import OrderBookChange, OrderBookDelta, OrderBookSnapshot, PriceLevel, Quote
from kairospy.domain.order import OrderEventKind
from kairospy.domain.intent import IntentJournal, IntentStatus, target_position_intent


AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


@dataclass
class LoginPort:
    def login(self, request: AccountLoginRequest) -> AccountLoginResult:
        return AccountLoginResult(AccountSession("session-1", request.context.segment, logged_in_at=request.observed_at))

    def logout(self, session: AccountSession) -> None:
        return None


def test_account_login_is_a_session_usecase() -> None:
    context = AccountRuntimeContext(AccountSegment("binance", "acct"), Environment.LIVE)
    account = AccountApplicationService((context,), login_port=LoginPort())

    result = account.login(context.segment, credential_ref="credential-1", at=AT)

    assert result.session.session_id == "session-1"
    assert result.session.account == context.segment


class OrderEntry:
    def submit(self, request: ConnectionOrderSubmissionRequest) -> ConnectionOrderSubmissionResult:
        assert request.symbol == "BTCUSDT"
        return ConnectionOrderSubmissionResult("venue-1", "NEW")

    def cancel(self, request: ConnectionOrderCancelRequest) -> ConnectionOrderCancelResult:
        return ConnectionOrderCancelResult(request.order_venue_id, "CANCELED")


@dataclass
class ExecutionContext:
    now: datetime
    intents: IntentJournal


class SupervisorProbe:
    def __init__(self) -> None:
        self.events: list[Message] = []

    async def dispatch(self, event: Message) -> None:
        self.events.append(event)


class OutputProbe:
    def publish_cycle(self, cycle: object, views: object) -> None:
        return None


def test_execution_converts_intent_to_order_and_delegates_submit() -> None:
    context = AccountRuntimeContext(AccountSegment("binance", "acct"), Environment.LIVE)
    intent = target_position_intent(
        strategy_id="strategy-1",
        instrument_id="BTCUSDT",
        account_segment="spot",
        target_quantity=Decimal("1"),
        at=AT,
        intent_id="intent-1",
    )
    journal = IntentJournal()
    journal.record_intent(intent, at=AT)
    execution = ExecutionApplication.compose(
        build_execution_coordinator(),
        order_connection=OrderEntry(),
        symbol_resolver=lambda instrument: "BTCUSDT",
        intents=journal,
    )

    order = execution.execute_intent(
        ExecuteIntentCommand(
            intent=intent,
            context=ExecutionContext(AT, journal),
            account=context,
            current_quantity=Decimal("0"),
            safety_policy=ExecutionSafetyPolicy(trading_enabled=True, require_limit_orders=False),
        )
    )

    assert order is not None
    assert order.order_venue_id == "venue-1"
    assert journal.get("intent-1").status is IntentStatus.ORDERING


def test_system_business_dispatches_market_events_to_account_actor() -> None:
    supervisor = SupervisorProbe()
    business = SystemBusinessRuntime(output=OutputProbe(), actor_supervisor=supervisor)
    business.runtime = SimpleNamespace(
        views=ViewStore(),
        observe=lambda event: None,
        process=lambda event, hook: SimpleNamespace(dispatched=False, hook=hook, output=None),
    )

    asyncio.run(business.process_async(Message("market.quote", object(), AT, "market", 1)))

    assert tuple(event.topic for event in supervisor.events) == ("market.quote",)


def test_account_commands_use_the_actor_mailbox() -> None:
    intent = target_position_intent(
        strategy_id="strategy-1",
        instrument_id="BTCUSDT",
        target_quantity=Decimal("1"),
        at=AT,
        intent_id="mailbox-intent",
    )
    actor = AccountActor(None, None)

    async def scenario() -> None:
        await actor.start()
        try:
            result = await actor.dispatch_command(RecordIntentsCommand((intent,), AT))
            assert result is None
            assert actor.intents.get("mailbox-intent").intent.intent_id == "mailbox-intent"
        finally:
            await actor.stop()

    asyncio.run(scenario())


def test_account_view_reflects_execution_pending_orders_and_fills() -> None:
    context = AccountRuntimeContext(AccountSegment("binance", "acct"), Environment.PAPER)
    coordinator = build_execution_coordinator()
    account_service = AccountApplicationService((context,), ledger=coordinator.ledger)
    account_runtime = RuntimeAccountService(
        views=RuntimeAccountViewProjectionService(
            account_service,
            account_directory=AccountDirectory.from_contexts((context,)),
        )
    )
    execution_application = ExecutionApplication.compose(coordinator)
    updates, projection = execution_runtime_adapters(execution_application)
    execution_runtime = RuntimeExecutionService(
        trading=TradingRuntimeExecutionService(updates=updates, projection=projection)
    )
    projectors = AccountActorProjectors(
        strategy_id="strategy-1",
        intents=IntentJournal(),
        account=account_runtime,
        execution=execution_runtime,
    )
    views = ViewStore()
    projectors.register_views(views)

    order = OrderRequest(
        "order-view-1",
        context,
        "BTCUSDT",
        OrderSide.BUY,
        Decimal("1"),
        OrderType.MARKET,
    )
    execution_application.plan_order(
        PlanOrderCommand(order, AT)
    )
    projectors.publish_views(views, as_of=AT)
    pending = AccountViewQueryService(views).pending_orders(account=context.segment.value)
    assert tuple(item.order_id for item in pending) == ("order-view-1",)
    assert tuple(item.order_id for item in AccountViewQueryService(views).open_orders(account=context.segment.value)) == ("order-view-1",)

    execution_application.submit_order(
        SubmitOrderCommand("order-view-1", AT)
    )
    execution_application.apply_update(
        ExecutionUpdate(
            AT,
            OrderEventKind.ACKNOWLEDGED,
            order_id="order-view-1",
            order_venue_id="venue-view-1",
        )
    )
    execution_application.apply_update(
        ExecutionUpdate(
            AT,
            OrderEventKind.FILLED,
            order_id="order-view-1",
            order_venue_id="venue-view-1",
            fill_quantity=Decimal("1"),
            fill_price=Decimal("100"),
            settlement_currency="USD",
            balance_delta=Decimal("-100"),
        )
    )
    projectors.publish_views(views, as_of=AT)

    assert AccountViewQueryService(views).pending_orders(account=context.segment.value) == ()
    strategy = StrategyContext("strategy-1", views=views)
    position = strategy.accounts.position("BTCUSDT", account=context.segment.value)
    assert position is not None
    assert position.quantity == Decimal("1")


def test_duplicate_execution_fill_is_applied_to_ledger_once() -> None:
    context = AccountRuntimeContext(AccountSegment("binance", "acct"), Environment.PAPER)
    coordinator = build_execution_coordinator()
    execution = ExecutionApplication.compose(coordinator)
    order = OrderRequest(
        "order-dedup-1",
        context,
        "BTCUSDT",
        OrderSide.BUY,
        Decimal("2"),
        OrderType.MARKET,
    )
    execution.plan_order(PlanOrderCommand(order, AT))
    execution.submit_order(SubmitOrderCommand(order.order_id, AT))
    execution.apply_update(
        ExecutionUpdate(AT, OrderEventKind.ACKNOWLEDGED, order_id=order.order_id, order_venue_id="venue-dedup-1")
    )
    fill = ExecutionUpdate(
        AT,
        OrderEventKind.PARTIALLY_FILLED,
        order_id=order.order_id,
        order_venue_id="venue-dedup-1",
        fill_quantity=Decimal("1"),
        fill_price=Decimal("100"),
        settlement_currency="USD",
        balance_delta=Decimal("-100"),
        metadata={"trade_id": "trade-dedup-1"},
    )
    first = execution.apply_update(fill)
    second = execution.apply_update(fill)

    assert first == second
    assert coordinator.ledger.positions(context.segment)["BTCUSDT"] == Decimal("1")


def test_account_actor_cancel_intent_cancels_linked_order() -> None:
    context = AccountRuntimeContext(AccountSegment("binance", "acct"), Environment.LIVE)
    intent = target_position_intent(
        strategy_id="strategy-cancel",
        instrument_id="BTCUSDT",
        target_quantity=Decimal("1"),
        at=AT,
        intent_id="intent-cancel-1",
    )
    journal = IntentJournal()
    journal.record_intent(intent, at=AT)
    coordinator = build_execution_coordinator()
    execution = ExecutionApplication.compose(
        coordinator,
        order_connection=OrderEntry(),
        symbol_resolver=lambda instrument: "BTCUSDT",
        intents=journal,
    )

    class Accounts:
        def accounts(self) -> tuple[AccountRuntimeContext, ...]:
            return (context,)

        def snapshot(self, account: AccountSegment):
            return None

    actor = AccountActor(
        None,
        None,
        account_application=Accounts(),
        execution_application=execution,
        intents=journal,
    )
    execution.execute_intent(
        ExecuteIntentCommand(
            intent=intent,
            context=type("ExecutionContext", (), {"now": AT, "intents": journal})(),
            account=context,
            current_quantity=Decimal("0"),
            safety_policy=ExecutionSafetyPolicy(trading_enabled=True, require_limit_orders=False),
        )
    )
    canceled = actor.cancel_intent(intent.intent_id, at=AT)

    assert canceled.status is IntentStatus.CANCELED
    assert execution.orders(context.segment)[0].status.value == "canceled"


def test_strategy_context_reads_intent_projection_from_runtime_views() -> None:
    intent = target_position_intent(
        strategy_id="strategy-view",
        instrument_id="BTCUSDT",
        account_id="acct",
        target_quantity=Decimal("2"),
        at=AT,
        intent_id="intent-view-1",
        reason="rebalance",
    )
    journal = IntentJournal()
    journal.record_intent(intent, at=AT)
    from kairospy.application.usecases.intent.services.runtime import IntentProjector

    projector = IntentProjector(strategy_id="strategy-view", intents=journal)
    views = ViewStore()
    projector.register_views(views)
    projector.publish_views(views, as_of=AT)

    context = StrategyContext("strategy-view", views=views)
    assert context.intents is not None
    assert context.intents.states[0].intent_id == "intent-view-1"
    assert context.intents.states[0].target_quantity == Decimal("2")
    assert context.intents.states[0].reason == "rebalance"


def test_paper_execution_fills_from_market_view_and_satisfies_intent() -> None:
    account = SimulatedAccount(
        "paper-account",
        initial_balances=(InitialAssetBalance("USD", Decimal("1000")),),
        broker="paper",
        environment=Environment.PAPER,
    )
    coordinator = build_execution_coordinator()
    execution = PaperExecutionService(
        coordinator,
        account=account.context,
        settlement_asset="USD",
        price_field="ask",
    )
    quote = Quote("BTCUSDT", AT, bid=Decimal("99"), ask=Decimal("100"))
    window = SimpleNamespace(latest=quote)
    market = SimpleNamespace(
        quotes=lambda _: window,
        bars=lambda _: SimpleNamespace(latest=None),
        trades=lambda _: SimpleNamespace(latest=None),
        rates=lambda _: SimpleNamespace(latest=None),
    )
    intent = target_position_intent(
        strategy_id="paper-strategy",
        instrument_id="BTCUSDT",
        target_quantity=Decimal("2"),
        at=AT,
        intent_id="paper-intent",
    )
    journal = IntentJournal()
    journal.record_intent(intent, at=AT)
    actor = AccountActor(
        None,
        None,
        execution_source=execution,
        account_application=PaperAccountService(account, coordinator.ledger),
        execution_application=ExecutionApplication.compose(coordinator, intents=journal),
        intents=journal,
    )

    fill = actor.execute_intent(intent, SimpleNamespace(now=AT, intents=journal, market=market))

    assert fill is None
    asyncio.run(actor.process(Message("market.quote", quote, AT, "test", 1)))
    events = execution.events()

    async def drain() -> None:
        await actor.process(await anext(events))
        await actor.process(await anext(events))

    asyncio.run(drain())
    assert execution.fills[-1].quantity == Decimal("2")
    assert coordinator.ledger.positions(account.context.segment)["BTCUSDT"] == Decimal("2")
    assert journal.get("paper-intent").status is IntentStatus.SATISFIED


def test_paper_cancel_emits_cancel_confirmation_before_pending_fill() -> None:
    account = SimulatedAccount(
        "paper-cancel-account",
        initial_balances=(InitialAssetBalance("USD", Decimal("1000")),),
        broker="paper",
        environment=Environment.PAPER,
    )
    coordinator = build_execution_coordinator()
    execution = PaperExecutionService(coordinator, account=account.context, settlement_asset="USD", price_field="ask")
    quote = Quote("BTCUSDT", AT, bid=Decimal("99"), ask=Decimal("100"))
    window = SimpleNamespace(latest=quote)
    market = SimpleNamespace(
        quotes=lambda _: window,
        bars=lambda _: SimpleNamespace(latest=None),
        trades=lambda _: SimpleNamespace(latest=None),
        rates=lambda _: SimpleNamespace(latest=None),
    )
    intent = target_position_intent(
        strategy_id="paper-cancel-strategy",
        instrument_id="BTCUSDT",
        target_quantity=Decimal("2"),
        at=AT,
        intent_id="paper-cancel-intent",
    )
    journal = IntentJournal()
    journal.record_intent(intent, at=AT)
    actor = AccountActor(
        None,
        None,
        execution_source=execution,
        account_application=PaperAccountService(account, coordinator.ledger),
        execution_application=ExecutionApplication.compose(coordinator, intents=journal),
        intents=journal,
    )
    actor.execute_intent(intent, SimpleNamespace(now=AT, intents=journal, market=market))
    actor.cancel_intent(intent.intent_id, at=AT)
    events = execution.events()

    async def drain_cancel() -> None:
        await actor.process(await anext(events))

    asyncio.run(drain_cancel())

    assert coordinator.orders.get("paper-cancel-intent-order").status.value == "canceled"
    assert coordinator.ledger.positions(account.context.segment) == {}
    assert journal.get("paper-cancel-intent").status is IntentStatus.CANCELED


def test_paper_orderbook_consumes_liquidity_and_keeps_intent_open_until_filled() -> None:
    account = SimulatedAccount(
        "paper-book-account",
        initial_balances=(InitialAssetBalance("USD", Decimal("1000")),),
        broker="paper",
        environment=Environment.PAPER,
    )
    coordinator = build_execution_coordinator()
    execution = PaperExecutionService(coordinator, account=account.context, settlement_asset="USD", price_field="ask")
    intent = target_position_intent(
        strategy_id="paper-book-strategy",
        instrument_id="BTCUSDT",
        target_quantity=Decimal("3"),
        at=AT,
        intent_id="paper-book-intent",
    )
    journal = IntentJournal()
    journal.record_intent(intent, at=AT)
    actor = AccountActor(
        None,
        None,
        execution_source=execution,
        account_application=PaperAccountService(account, coordinator.ledger),
        execution_application=ExecutionApplication.compose(coordinator, intents=journal),
        intents=journal,
    )
    actor.execute_intent(intent, SimpleNamespace(now=AT, intents=journal))
    first_book = OrderBookSnapshot(
        "BTCUSDT",
        AT,
        bids=(PriceLevel(Decimal("99"), Decimal("5")),),
        asks=(PriceLevel(Decimal("100"), Decimal("1")),),
    )
    second_book = OrderBookDelta(
        "BTCUSDT",
        AT,
        changes=(
            OrderBookChange("ask", Decimal("100"), Decimal("0")),
            OrderBookChange("ask", Decimal("101"), Decimal("2")),
        ),
    )

    async def apply_market_and_execution() -> tuple[OrderStatus, IntentStatus, Decimal]:
        events = execution.events()
        await actor.process(await anext(events))
        wrong_book = OrderBookSnapshot(
            "ETHUSDT",
            AT,
            bids=(PriceLevel(Decimal("1999"), Decimal("5")),),
            asks=(PriceLevel(Decimal("2000"), Decimal("5")),),
        )
        await actor.process(Message("market.orderbook", wrong_book, AT, "test", 1))
        assert execution._events.empty()
        await actor.process(Message("market.orderbook", first_book, AT, "test", 1))
        await actor.process(await anext(events))
        first_status = coordinator.orders.get("paper-book-intent-order").status
        first_intent_status = journal.get("paper-book-intent").status
        first_position = coordinator.ledger.positions(account.context.segment)["BTCUSDT"]

        await actor.process(Message("market.orderbook.delta", second_book, AT, "test", 2))
        await actor.process(await anext(events))
        return first_status, first_intent_status, first_position

    first_status, first_intent_status, first_position = asyncio.run(apply_market_and_execution())
    assert first_status is OrderStatus.PARTIALLY_FILLED
    assert first_intent_status is IntentStatus.PARTIALLY_FILLED
    assert first_position == Decimal("1")

    assert coordinator.orders.get("paper-book-intent-order").status is OrderStatus.FILLED
    assert journal.get("paper-book-intent").status is IntentStatus.SATISFIED
    assert coordinator.ledger.positions(account.context.segment)["BTCUSDT"] == Decimal("3")

    sell_intent = target_position_intent(
        strategy_id="paper-book-strategy",
        instrument_id="BTCUSDT",
        target_quantity=Decimal("2"),
        at=AT,
        intent_id="paper-book-sell-intent",
    )
    journal.record_intent(sell_intent, at=AT)
    actor.execute_intent(sell_intent, SimpleNamespace(now=AT, intents=journal))
    sell_book = OrderBookSnapshot(
        "BTCUSDT",
        AT,
        bids=(PriceLevel(Decimal("98"), Decimal("1")),),
        asks=(PriceLevel(Decimal("99"), Decimal("5")),),
    )

    async def apply_sell() -> None:
        events = execution.events()
        await actor.process(await anext(events))
        await actor.process(Message("market.orderbook", sell_book, AT, "test", 3))
        await actor.process(await anext(events))

    asyncio.run(apply_sell())
    assert execution.fills[-1].price == Decimal("98")
    assert coordinator.ledger.positions(account.context.segment)["BTCUSDT"] == Decimal("2")


def test_paper_orderbook_does_not_double_count_top_level_liquidity() -> None:
    account = SimulatedAccount(
        "paper-liquidity-account",
        initial_balances=(InitialAssetBalance("USD", Decimal("1000")),),
        broker="paper",
        environment=Environment.PAPER,
    )
    coordinator = build_execution_coordinator()
    execution = PaperExecutionService(coordinator, account=account.context, settlement_asset="USD", price_field="ask")
    journal = IntentJournal()
    intents = tuple(
        target_position_intent(
            strategy_id="paper-liquidity-strategy",
            instrument_id="BTCUSDT",
            target_quantity=Decimal("1"),
            at=AT,
            intent_id=f"paper-liquidity-intent-{index}",
        )
        for index in (1, 2)
    )
    for intent in intents:
        journal.record_intent(intent, at=AT)
    actor = AccountActor(
        None,
        None,
        execution_source=execution,
        account_application=PaperAccountService(account, coordinator.ledger),
        execution_application=ExecutionApplication.compose(coordinator, intents=journal),
        intents=journal,
    )
    for intent in intents:
        actor.execute_intent(intent, SimpleNamespace(now=AT, intents=journal))
    book = OrderBookSnapshot(
        "BTCUSDT",
        AT,
        bids=(PriceLevel(Decimal("99"), Decimal("5")),),
        asks=(PriceLevel(Decimal("100"), Decimal("1")),),
    )

    async def apply_market() -> None:
        events = execution.events()
        await actor.process(await anext(events))
        await actor.process(await anext(events))
        await actor.process(Message("market.orderbook", book, AT, "test", 1))
        await actor.process(await anext(events))

    asyncio.run(apply_market())
    assert coordinator.ledger.positions(account.context.segment)["BTCUSDT"] == Decimal("1")
    assert coordinator.orders.get("paper-liquidity-intent-1-order").status is OrderStatus.FILLED
    assert coordinator.orders.get("paper-liquidity-intent-2-order").status is OrderStatus.ACKNOWLEDGED
    assert execution._events.empty()


def test_paper_rejects_buy_intent_when_market_price_exceeds_cash() -> None:
    account = SimulatedAccount(
        "paper-buying-power-account",
        initial_balances=(InitialAssetBalance("USD", Decimal("1000")),),
        broker="paper",
        environment=Environment.PAPER,
    )
    coordinator = build_execution_coordinator()
    execution = PaperExecutionService(coordinator, account=account.context, settlement_asset="USD", price_field="ask")
    journal = IntentJournal()
    intent = target_position_intent(
        strategy_id="paper-buying-power-strategy",
        instrument_id="BTCUSDT",
        target_quantity=Decimal("11"),
        at=AT,
        intent_id="paper-buying-power-intent",
    )
    journal.record_intent(intent, at=AT)
    actor = AccountActor(
        None,
        None,
        execution_source=execution,
        account_application=PaperAccountService(account, coordinator.ledger),
        execution_application=ExecutionApplication.compose(coordinator, intents=journal),
        intents=journal,
    )
    execution.on_market_event(Quote("BTCUSDT", AT, bid=Decimal("99"), ask=Decimal("100")))

    actor.execute_intent(intent, SimpleNamespace(now=AT, intents=journal))

    assert coordinator.orders.get("paper-buying-power-intent-order").status is OrderStatus.REJECTED
    assert journal.get(intent.intent_id).status is IntentStatus.FAILED
    assert coordinator.ledger.positions(account.context.segment) == {}
    assert coordinator.ledger.balances(account.context.segment)["USD"] == Decimal("1000")


def test_paper_applies_reference_market_rules_at_submission() -> None:
    account = SimulatedAccount(
        "paper-rules-account",
        initial_balances=(InitialAssetBalance("USD", Decimal("1000")),),
        broker="paper",
        environment=Environment.PAPER,
    )
    coordinator = build_execution_coordinator()
    reference = SimpleNamespace(
        market_definition=lambda market_id, *, at: SimpleNamespace(
            status="active",
            amount_tick=Decimal("1"),
            price_tick=Decimal("0.5"),
            min_amount=Decimal("1"),
            min_notional=Decimal("0"),
        )
    )
    execution = PaperExecutionService(
        coordinator,
        account=account.context,
        settlement_asset="USD",
        price_field="ask",
        market_reference=reference,
    )
    journal = IntentJournal()
    intent = target_position_intent(
        strategy_id="paper-rules-strategy",
        instrument_id="BTCUSDT",
        market_id="market-btcusdt",
        target_quantity=Decimal("1.5"),
        at=AT,
        intent_id="paper-rules-intent",
    )
    journal.record_intent(intent, at=AT)
    actor = AccountActor(
        None,
        None,
        execution_source=execution,
        account_application=PaperAccountService(account, coordinator.ledger),
        execution_application=ExecutionApplication.compose(coordinator, intents=journal),
        intents=journal,
    )

    actor.execute_intent(intent, SimpleNamespace(now=AT, intents=journal))

    assert coordinator.orders.get("paper-rules-intent-order").status is OrderStatus.REJECTED
    assert journal.get(intent.intent_id).status is IntentStatus.FAILED
    assert execution._events.empty()


def test_execution_state_restore_preserves_paper_account_ledger_identity() -> None:
    account = SimulatedAccount(
        "paper-restore-account",
        initial_balances=(InitialAssetBalance("USD", Decimal("1000")),),
        broker="paper",
        environment=Environment.PAPER,
    )
    coordinator = build_execution_coordinator()
    account_service = PaperAccountService(account, coordinator.ledger)
    snapshot = ExecutionStateSnapshot.capture(coordinator)
    coordinator.ledger.record(
        AccountEvent(
            uuid4(),
            account.context.segment,
            AccountEventKind.DEPOSIT,
            AT,
            "USD",
            balance_delta=Decimal("10"),
        )
    )

    snapshot.restore_into(coordinator)

    assert account_service.state().balance("USD").total == Decimal("1000")
    assert coordinator.ledger is account_service.ledger


def test_cancel_execution_update_releases_paper_reservation() -> None:
    account = SimulatedAccount(
        "paper-reservation-account",
        initial_balances=(InitialAssetBalance("USD", Decimal("1000")),),
        broker="paper",
        environment=Environment.PAPER,
    )
    coordinator = ExecutionCoordinator()
    PaperAccountService(account, coordinator.ledger)
    execution = PaperExecutionService(coordinator, account=account.context, settlement_asset="USD", price_field="ask")
    request = OrderRequest(
        "paper-reserved-order",
        account.context,
        "BTCUSDT",
        OrderSide.BUY,
        Decimal("1"),
        OrderType.MARKET,
    )
    coordinator.plan_order(
        request,
        reserve_currency="USD",
        reserve_amount=Decimal("100"),
        at=AT,
    )
    assert coordinator.reservations.active_amounts(account.context.segment) == {"USD": Decimal("100")}

    execution.cancel_order(request.order_id, at=AT)
    events = execution.events()
    execution_application = ExecutionApplication.compose(coordinator)

    async def drain_cancel() -> None:
        execution_application.apply_update((await anext(events)).payload)

    asyncio.run(drain_cancel())

    assert coordinator.reservations.active_amounts(account.context.segment) == {}
