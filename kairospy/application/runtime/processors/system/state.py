from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.application.runtime.ports import AccountJournalSink, AccountPort, MarketDataPort, ReferencePort, TradingExecutionPort
from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.application.runtime.processors.account import AccountProcessor, EquityCurveProcessor, FundingProcessor
from kairospy.application.runtime.processors.execution import ExecutionProcessor, TradingIntentProcessor
from kairospy.application.runtime.processors.intent import IntentProcessor
from kairospy.application.runtime.processors.journal import AccountJournalProcessor
from kairospy.application.runtime.processors.market import MarketProcessor
from kairospy.application.runtime.processors.order import OrderProcessor
from kairospy.application.runtime.processors.reference import ReferenceProcessor
from kairospy.application.runtime.processors.risk import RiskProcessor
from kairospy.application.runtime.processors.timeline import TimelineProcessor
from kairospy.application.runtime.processors.trace import TraceProcessor
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.intent import IntentJournal
from kairospy.core.views import ViewStore

from .processor import SystemProcessor


@dataclass(frozen=True, slots=True)
class RuntimeProcessors:
    system: SystemProcessor
    intent: IntentProcessor
    risk: RiskProcessor
    market: MarketProcessor | None = None
    funding: FundingProcessor | None = None
    equity: EquityCurveProcessor | None = None
    account: AccountProcessor | None = None
    reference: ReferenceProcessor | None = None
    execution: ExecutionProcessor | None = None
    order: OrderProcessor | None = None
    trading_intent: TradingIntentProcessor | None = None
    account_journal: AccountJournalProcessor | None = None
    trace: TraceProcessor | None = None
    timeline: TimelineProcessor | None = None

    def on_event(self, event: RuntimeEnvelope) -> None:
        self.system.on_event(event)
        self.intent.on_event(event)
        self.risk.on_event(event)
        if self.market is not None:
            self.market.on_event(event)
        if self.funding is not None:
            self.funding.on_event(event)
        if self.equity is not None:
            self.equity.on_event(event)
        if self.account is not None:
            self.account.on_event(event)
        if self.reference is not None:
            self.reference.on_event(event)
        if self.execution is not None:
            self.execution.on_event(event)
        if self.order is not None:
            self.order.on_event(event)
        if self.trading_intent is not None:
            self.trading_intent.on_event(event)
        if self.trace is not None:
            self.trace.on_event(event)
        if self.timeline is not None:
            self.timeline.on_event(event)

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        if self.trading_intent is not None:
            self.trading_intent.on_intents(intents, context, hook)
        if self.equity is not None:
            self.equity.on_intents(context)
        if self.trace is not None:
            self.trace.on_intents(intents, context, hook)
        if self.timeline is not None:
            self.timeline.on_intents(intents, context, hook)

    def register_views(self, views: ViewStore) -> None:
        self.system.register_views(views)
        self.intent.register_views(views)
        self.risk.register_views(views)
        if self.market is not None:
            self.market.register_views(views)
        if self.equity is not None:
            self.equity.register_views(views)
        if self.account is not None:
            self.account.register_views(views)
        if self.reference is not None:
            self.reference.register_views(views)
        if self.execution is not None:
            self.execution.register_views(views)
        if self.order is not None:
            self.order.register_views(views)
        if self.trace is not None:
            self.trace.register_views(views)

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        self.system.publish_views(views, as_of=as_of)
        self.intent.publish_views(views, as_of=as_of)
        self.risk.publish_views(views, as_of=as_of)
        if self.market is not None:
            self.market.publish_views(views, as_of=as_of)
        if self.equity is not None:
            self.equity.publish_views(views, as_of=as_of)
        if self.account is not None:
            self.account.publish_views(views, as_of=as_of)
        if self.reference is not None:
            self.reference.publish_views(views, as_of=as_of)
        if self.execution is not None:
            self.execution.publish_views(views, as_of=as_of)
        if self.order is not None:
            self.order.publish_views(views, as_of=as_of)
        if self.account_journal is not None:
            self.account_journal.publish_views(views, as_of=as_of)
        if self.trace is not None:
            self.trace.publish_views(views, as_of=as_of)
        if self.timeline is not None:
            self.timeline.publish_views(views, as_of=as_of)


def runtime_processors(
    *,
    strategy_id: str,
    intents: IntentJournal,
    data: MarketDataPort | None = None,
    account: AccountPort | None = None,
    reference: ReferencePort | None = None,
    trading_execution: TradingExecutionPort | None = None,
    execution_coordinator: ExecutionCoordinator | None = None,
    account_journal: AccountJournalSink | None = None,
    timeline_journal: object | None = None,
    timeline_sample_interval: object = "1m",
) -> RuntimeProcessors:
    account_processor = None if account is None else AccountProcessor(account)
    funding_processor = _funding_processor(account, execution_coordinator)
    equity_processor = _equity_processor(account, execution_coordinator)
    trace_processor = _trace_processor(account, execution_coordinator)
    return RuntimeProcessors(
        system=SystemProcessor(strategy_id=strategy_id),
        intent=IntentProcessor(strategy_id=strategy_id, intents=intents),
        risk=RiskProcessor(),
        market=None if data is None else MarketProcessor(data),
        funding=funding_processor,
        equity=equity_processor,
        account=account_processor,
        reference=None if reference is None else ReferenceProcessor(reference),
        execution=None if execution_coordinator is None else ExecutionProcessor(execution_coordinator),
        order=None if execution_coordinator is None else OrderProcessor(execution_coordinator),
        trading_intent=(
            TradingIntentProcessor(trading_execution)
            if trading_execution is not None and callable(getattr(trading_execution, "execute_intent", None))
            else None
        ),
        account_journal=(
            None
            if account_journal is None or account_processor is None
            else AccountJournalProcessor(account_journal, account_view_keys=tuple(state.key for state in account_processor.states))
        ),
        trace=trace_processor,
        timeline=(
            None
            if timeline_journal is None
            else TimelineProcessor(timeline_journal, sample_interval=timeline_sample_interval)
        ),
    )


def _funding_processor(account: AccountPort | None, coordinator: ExecutionCoordinator | None) -> FundingProcessor | None:
    if account is None or coordinator is None:
        return None
    accounts = account.accounts()
    if len(accounts) != 1:
        return None
    context = accounts[0]
    if getattr(context.environment, "value", str(context.environment)) != "backtest":
        return None
    service_account = getattr(account, "account", None)
    settlement_currency = str(getattr(service_account, "cash_currency", "") or "USD")
    return FundingProcessor(account=context, coordinator=coordinator, settlement_currency=settlement_currency)


def _equity_processor(account: AccountPort | None, coordinator: ExecutionCoordinator | None) -> EquityCurveProcessor | None:
    if account is None or coordinator is None:
        return None
    accounts = account.accounts()
    if len(accounts) != 1:
        return None
    service_account = getattr(account, "account", None)
    cash_currency = str(getattr(service_account, "cash_currency", "") or "USD")
    return EquityCurveProcessor(account=accounts[0], coordinator=coordinator, cash_currency=cash_currency)


def _trace_processor(account: AccountPort | None, coordinator: ExecutionCoordinator | None) -> TraceProcessor:
    if account is None or coordinator is None:
        return TraceProcessor()
    accounts = account.accounts()
    if len(accounts) != 1:
        return TraceProcessor()
    service_account = getattr(account, "account", None)
    cash_currency = str(getattr(service_account, "cash_currency", "") or "USD")
    return TraceProcessor(account=accounts[0], coordinator=coordinator, cash_currency=cash_currency)


__all__ = ["RuntimeProcessors", "runtime_processors"]
