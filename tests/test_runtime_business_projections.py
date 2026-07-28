from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from decimal import Decimal

from kairospy.application.runtime.orchestration.pipeline import RuntimeServicePipeline
from kairospy.application.runtime.orchestration.kernel import RuntimeKernel
from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.application.runtime.services import (
    AccountServiceProjectionProvider,
    DataSubscription,
    MarketDataProjectionProvider,
    MarketDataSubscriptionSpec,
    ReferenceServiceProjectionProvider,
)
from kairospy.application.service.engine.backtest import BacktestExecutionService
from kairospy.core.account import AccountBalance, AccountContext, AccountRef, AccountSnapshot, AccountSource, AccountState, Environment
from kairospy.core.execution import ExecutionCoordinator, cash_order_request
from kairospy.core.intent import IntentJournal
from kairospy.core.market import Bar, Quote
from kairospy.core.order import OrderSide
from kairospy.core.reference import MarketRef, MarketResolver, ReferenceCatalog
from kairospy.core.views import ViewSchema, ViewStore


class EmptyStrategy:
    strategy_id = "s"

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


class FakeMarketDataService:
    key = "fake.market"
    schema = ViewSchema(key, "system", mutability="runtime_writable")

    def __init__(self) -> None:
        self.market = MarketRef.ephemeral(venue="binance", market="spot", source_symbol="BTC/USDT")
        self.subscription = DataSubscription(
            "sub.btc",
            MarketDataSubscriptionSpec(self.market, (Quote, Bar), identity="strategy-a"),
        )

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        if False:
            yield

    def subscribe(self, spec: MarketDataSubscriptionSpec) -> DataSubscription:
        return DataSubscription(spec.key, spec)

    def unsubscribe(self, subscription: DataSubscription | str) -> None:
        return None

    def subscriptions(self) -> tuple[DataSubscription, ...]:
        return (self.subscription,)

    def on_event(self, event: RuntimeEnvelope) -> None:
        return None

    def view(self) -> dict[str, object]:
        return {}


class FakeAccountService:
    key = "fake.account"
    schema = ViewSchema(key, "system", mutability="runtime_writable")

    def __init__(self) -> None:
        self.context = AccountContext(AccountRef("paper", "main"), Environment.PAPER)
        self._snapshot = AccountSnapshot(
            self.context,
            balances=(AccountBalance.from_total_locked("USDT", Decimal("1000"), Decimal("0"), source=AccountSource.SIMULATED),),
            observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            source=AccountSource.SIMULATED,
        )
        self._state = AccountState(
            self.context,
            self._snapshot.balances,
            (),
            (),
            (),
            self._snapshot.observed_at,
            AccountSource.SIMULATED,
        )

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        if False:
            yield

    def accounts(self) -> tuple[AccountContext, ...]:
        return (self.context,)

    def snapshot(self, account: AccountRef | None = None) -> AccountSnapshot | None:
        return self._snapshot

    def state(self, account: AccountRef | None = None) -> AccountState | None:
        return self._state

    def on_event(self, event: RuntimeEnvelope) -> None:
        return None

    def view(self) -> dict[str, object]:
        return {}


def test_market_projection_publishes_business_views() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    views = ViewStore()
    data = FakeMarketDataService()
    pipeline = RuntimeServicePipeline(
        views=views,
        strategy_id="s",
        intents=IntentJournal(),
        data=data,
        providers=(MarketDataProjectionProvider(data),),
    )
    event = RuntimeEnvelope(
        "market",
        "quote",
        now,
        1,
        Quote(
            instrument_id="instrument:spot:btc:usdt",
            market_id="market:binance:spot:btc_usdt",
            market_key="binance_spot_btc_usdt",
            time=now,
            bid=Decimal("100"),
            ask=Decimal("101"),
            source="binance",
        ),
    )

    pipeline.on_event(event)

    assert views.require("market.subscriptions").active_count == 1
    assert views.require("market.quotes").quotes[0].bid == Decimal("100")
    assert views.require("market.fields").fields
    assert views.require("market.observations").observations[0].kind == "quote"


def test_account_service_provider_publishes_current_account_view() -> None:
    views = ViewStore()
    account = FakeAccountService()
    pipeline = RuntimeServicePipeline(
        views=views,
        strategy_id="s",
        intents=IntentJournal(),
        account=account,
        providers=(AccountServiceProjectionProvider(account),),
    )

    pipeline.publish()

    current = views.require("account.current.paper.paper.main")
    assert current.context == account.context
    assert current.cash == Decimal("1000")
    assert current.source is AccountSource.SIMULATED


class FakeReferenceService:
    def __init__(self) -> None:
        self._catalog = ReferenceCatalog()

    def catalog(self) -> ReferenceCatalog:
        return self._catalog

    def resolver(self, *, as_of: datetime | None = None) -> MarketResolver:
        return MarketResolver(self._catalog, as_of=as_of) if as_of is not None else MarketResolver()

    def lifecycle_events(self) -> tuple[object, ...]:
        return ()


def test_reference_service_provider_publishes_catalog_view() -> None:
    views = ViewStore()
    reference = FakeReferenceService()
    pipeline = RuntimeServicePipeline(
        views=views,
        strategy_id="s",
        intents=IntentJournal(),
        reference=reference,
        providers=(ReferenceServiceProjectionProvider(reference),),
    )

    pipeline.publish()

    assert views.require("reference.catalog").market_count == 0
    assert views.registry.require("reference.catalog").evidence == "runtime reference catalog projection"


def test_kernel_wraps_runtime_services_as_component_providers() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    context = AccountContext(AccountRef("paper", "main"), Environment.PAPER)
    execution = ExecutionCoordinator()
    execution.plan_order(
        cash_order_request(
            client_order_id="order-1",
            context=context,
            instrument_id="instrument:spot:btc:usdt",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
        ),
        at=now,
    )

    kernel = RuntimeKernel(
        EmptyStrategy(),
        data=FakeMarketDataService(),
        account=FakeAccountService(),
        reference=FakeReferenceService(),
        execution=execution,
        providers=(BacktestExecutionService(execution),),
    )
    kernel.start()

    assert kernel.views.require("market.subscriptions").active_count == 1
    assert kernel.views.require("account.current.paper.paper.main").cash == Decimal("1000")
    assert kernel.views.require("reference.catalog").market_count == 0
    assert kernel.views.require("execution.current").total_orders == 1


def test_system_risk_execution_and_order_views_are_business_panels() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    context = AccountContext(AccountRef("paper", "main"), Environment.PAPER)
    execution = ExecutionCoordinator()
    execution.plan_order(
        cash_order_request(
            client_order_id="order-1",
            context=context,
            instrument_id="instrument:spot:btc:usdt",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
        ),
        at=now,
    )
    views = ViewStore()
    pipeline = RuntimeServicePipeline(
        views=views,
        strategy_id="s",
        intents=IntentJournal(),
        providers=(BacktestExecutionService(execution),),
    )

    pipeline.on_event(RuntimeEnvelope("system", "risk.limit_warn", now, 1, {"limit": "gross"}))

    assert views.require("system.strategy").event_count == 1
    assert views.require("system.events").last_name == "risk.limit_warn"
    assert views.require("risk.events").last_payload == {"limit": "gross"}
    assert views.require("execution.current").total_orders == 1
    assert views.require("order.current").execution.latest_order.client_order_id == "order-1"
