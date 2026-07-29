from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.application.runtime.ports import AccountJournalSink, AccountPort, MarketDataPort, ReferencePort, TradingExecutionPort
from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.application.runtime.processors.account import AccountProcessor
from kairospy.application.runtime.processors.execution import ExecutionProcessor, TradingIntentProcessor
from kairospy.application.runtime.processors.intent import IntentProcessor
from kairospy.application.runtime.processors.journal import AccountJournalProcessor
from kairospy.application.runtime.processors.market import MarketProcessor
from kairospy.application.runtime.processors.order import OrderProcessor
from kairospy.application.runtime.processors.reference import ReferenceProcessor
from kairospy.application.runtime.processors.risk import RiskProcessor
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
    account: AccountProcessor | None = None
    reference: ReferenceProcessor | None = None
    execution: ExecutionProcessor | None = None
    order: OrderProcessor | None = None
    trading_intent: TradingIntentProcessor | None = None
    account_journal: AccountJournalProcessor | None = None

    def on_event(self, event: RuntimeEnvelope) -> None:
        self.system.on_event(event)
        self.intent.on_event(event)
        self.risk.on_event(event)
        if self.market is not None:
            self.market.on_event(event)
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

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        if self.trading_intent is not None:
            self.trading_intent.on_intents(intents, context, hook)

    def register_views(self, views: ViewStore) -> None:
        self.system.register_views(views)
        self.intent.register_views(views)
        self.risk.register_views(views)
        if self.market is not None:
            self.market.register_views(views)
        if self.account is not None:
            self.account.register_views(views)
        if self.reference is not None:
            self.reference.register_views(views)
        if self.execution is not None:
            self.execution.register_views(views)
        if self.order is not None:
            self.order.register_views(views)

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        self.system.publish_views(views, as_of=as_of)
        self.intent.publish_views(views, as_of=as_of)
        self.risk.publish_views(views, as_of=as_of)
        if self.market is not None:
            self.market.publish_views(views, as_of=as_of)
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
) -> RuntimeProcessors:
    account_processor = None if account is None else AccountProcessor(account)
    return RuntimeProcessors(
        system=SystemProcessor(strategy_id=strategy_id),
        intent=IntentProcessor(strategy_id=strategy_id, intents=intents),
        risk=RiskProcessor(),
        market=None if data is None else MarketProcessor(data),
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
    )


__all__ = ["RuntimeProcessors", "runtime_processors"]
