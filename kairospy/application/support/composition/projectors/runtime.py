from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.application.support.runtime.application.views import ViewStore
from kairospy.application.support.runtime.domain.components import RuntimeComponents
from kairospy.application.support.runtime.domain.events import RuntimeEnvelope
from kairospy.application.support.runtime.services.orchestration.state import RuntimeStores
from kairospy.application.support.composition.application.runtime_services import RuntimeApplicationServices, RuntimeServiceDependencies
from kairospy.application.usecases.intent.application.projection import IntentProcessor
from kairospy.application.support.runtime.services.processors.risk import RiskProcessor
from kairospy.application.support.runtime.services.processors.system import SystemProcessor
from kairospy.application.support.runtime.services.processors.trace import TraceProcessor
from kairospy.domain.intent import IntentJournal

from .account import AccountProjector, EquityProjector, FundingProjector
from .execution import ExecutionProjector
from .execution_intent import TradingIntentProjector
from .market import MarketProjector
from .order import OrderProjector
from .reference import ReferenceProjector


@dataclass(frozen=True, slots=True)
class RuntimeProjectorSet:
    system: SystemProcessor
    intent: IntentProcessor
    risk: RiskProcessor
    market: MarketProjector | None = None
    funding: FundingProjector | None = None
    equity: EquityProjector | None = None
    account: AccountProjector | None = None
    reference: ReferenceProjector | None = None
    execution: ExecutionProjector | None = None
    order: OrderProjector | None = None
    trading_intent: TradingIntentProjector | None = None
    trace: TraceProcessor | None = None

    def _all(self) -> tuple[object, ...]:
        # Intent execution must happen before equity is sampled; otherwise the
        # equity projector records the pre-fill portfolio and suppresses the
        # post-fill point as a duplicate marker.
        return tuple(item for item in (self.system, self.intent, self.risk, self.market, self.funding, self.account, self.reference, self.execution, self.order, self.trading_intent, self.equity, self.trace) if item is not None)

    def on_event(self, event: RuntimeEnvelope) -> None:
        for projector in self._all():
            projector.on_event(event)

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        for projector in self._all():
            handler = getattr(projector, "on_intents", None)
            if handler is not None:
                handler(intents, context, hook)

    def register_views(self, views: ViewStore) -> None:
        for projector in self._all():
            projector.register_views(views)

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        for projector in self._all():
            projector.publish_views(views, as_of=as_of)


def runtime_projectors(*, strategy_id: str, intents: IntentJournal, services: RuntimeApplicationServices) -> RuntimeProjectorSet:
    account = None if services.account is None or services.account.views is None else AccountProjector(services.account)
    return RuntimeProjectorSet(
        system=SystemProcessor(strategy_id=strategy_id),
        intent=IntentProcessor(strategy_id=strategy_id, intents=intents),
        risk=RiskProcessor(),
        market=None if services.market is None else MarketProjector(services.market),
        funding=_funding(services),
        equity=_equity(services),
        account=account,
        reference=None if services.reference is None else ReferenceProjector(services.reference),
        execution=None if services.execution is None or not services.execution.has_projection or not services.execution.has_updates else ExecutionProjector(service=services.execution),
        order=None if services.execution is None or not services.execution.has_projection else OrderProjector(services.execution),
        trading_intent=None if services.execution is None or not services.execution.can_execute_intents else TradingIntentProjector(services.execution),
        trace=TraceProcessor(service=None if services.account is None or services.account.projection is None else services.account),
    )


def runtime_services_for(components: RuntimeComponents, stores: RuntimeStores) -> RuntimeApplicationServices:
    return RuntimeApplicationServices.from_dependencies(
        RuntimeServiceDependencies(
            intents=stores.intents,
            data=components.market,
            account_snapshot_store=components.account,
            account=components.account,
            account_catalog=components.account_catalog,
            account_directory=_account_directory(components),
            reference=components.reference,
            trading_execution=components.execution,
            execution_coordinator=_execution_coordinator(components),
            fills_source=components.execution,
        )
    )


def _execution_coordinator(components: RuntimeComponents) -> object | None:
    for candidate in (components.execution, components.account):
        coordinator = getattr(candidate, "coordinator", None)
        if coordinator is not None:
            return coordinator
    return None


def _account_directory(components: RuntimeComponents) -> object | None:
    catalog = components.account_catalog
    provider = getattr(catalog, "directory", None)
    return provider() if callable(provider) else None


def _funding(services: RuntimeApplicationServices) -> FundingProjector | None:
    if services.account is None or services.account.projection is None or getattr(services.account.account.environment, "value", str(services.account.account.environment)) != "backtest":
        return None
    return FundingProjector(service=services.account)


def _equity(services: RuntimeApplicationServices) -> EquityProjector | None:
    return None if services.account is None or services.account.projection is None else EquityProjector(service=services.account)


__all__ = ["RuntimeProjectorSet", "runtime_projectors", "runtime_services_for"]
