from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.application.support.runtime.events import RuntimeEnvelope
from kairospy.application.support.runtime.processors.account import AccountProcessor, EquityCurveProcessor, FundingProcessor
from kairospy.application.support.runtime.processors.execution import ExecutionProcessor, TradingIntentProcessor
from kairospy.application.support.runtime.processors.intent import IntentProcessor
from kairospy.application.support.runtime.processors.market import MarketProcessor
from kairospy.application.support.runtime.processors.order import OrderProcessor
from kairospy.application.support.runtime.processors.reference import ReferenceProcessor
from kairospy.application.support.runtime.processors.risk import RiskProcessor
from kairospy.application.support.runtime.processors.trace import TraceProcessor
from kairospy.application.support.runtime.services.application import RuntimeApplicationServices
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
    trace: TraceProcessor | None = None

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

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        if self.trading_intent is not None:
            self.trading_intent.on_intents(intents, context, hook)
        if self.equity is not None:
            self.equity.on_intents(context)
        if self.trace is not None:
            self.trace.on_intents(intents, context, hook)

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
        if self.trace is not None:
            self.trace.publish_views(views, as_of=as_of)


def runtime_processors(
    *,
    strategy_id: str,
    intents: IntentJournal,
    services: RuntimeApplicationServices | None = None,
) -> RuntimeProcessors:
    services = services or RuntimeApplicationServices()
    account_processor = None if services.account is None or services.account.views is None else AccountProcessor(services.account)
    funding_processor = _funding_processor(services)
    equity_processor = _equity_processor(services)
    trace_processor = _trace_processor(services)
    return RuntimeProcessors(
        system=SystemProcessor(strategy_id=strategy_id),
        intent=IntentProcessor(strategy_id=strategy_id, intents=intents),
        risk=RiskProcessor(),
        market=None if services.market is None else MarketProcessor(services.market),
        funding=funding_processor,
        equity=equity_processor,
        account=account_processor,
        reference=None if services.reference is None else ReferenceProcessor(services.reference),
        execution=(
            None
            if services.execution is None or not services.execution.has_projection or not services.execution.has_updates
            else ExecutionProcessor(
                service=services.execution,
            )
        ),
        order=None if services.execution is None or not services.execution.has_projection else OrderProcessor(services.execution),
        trading_intent=None if services.execution is None or not services.execution.can_execute_intents else TradingIntentProcessor(services.execution),
        trace=trace_processor,
    )


def _funding_processor(services: RuntimeApplicationServices) -> FundingProcessor | None:
    if services.account is None or services.account.projection is None:
        return None
    context = services.account.account
    if getattr(context.environment, "value", str(context.environment)) != "backtest":
        return None
    return FundingProcessor(service=services.account)


def _equity_processor(services: RuntimeApplicationServices) -> EquityCurveProcessor | None:
    if services.account is None or services.account.projection is None:
        return None
    return EquityCurveProcessor(service=services.account)


def _trace_processor(services: RuntimeApplicationServices) -> TraceProcessor:
    return TraceProcessor(service=None if services.account is None or services.account.projection is None else services.account)


__all__ = ["RuntimeProcessors", "runtime_processors"]
