from __future__ import annotations

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.application.runtime.projection import (
    IntentJournalProjection,
    RiskEventProjection,
    RuntimeSystemProjection,
    SystemEventProjection,
)
from kairospy.application.runtime.services import AccountService, MarketDataService, ReferenceService
from kairospy.application.runtime.services.component import (
    RuntimeComponentProvider,
    RuntimeIntentProcessor,
    RuntimeViewPublisher,
    provided_components,
    publish_components,
    register_components,
)
from kairospy.core.intent import IntentJournal
from kairospy.core.views import ViewStore


class RuntimeServicePipeline:
    def __init__(
        self,
        *,
        views: ViewStore,
        strategy_id: str,
        intents: IntentJournal,
        data: MarketDataService | None = None,
        account: AccountService | None = None,
        reference: ReferenceService | None = None,
        providers: tuple[RuntimeComponentProvider, ...] = (),
        components: tuple[RuntimeViewPublisher, ...] = (),
    ) -> None:
        self.views = views
        self.data = data
        self.account = account
        self.reference = reference
        self.services = _runtime_services(data, account)
        self.intent_processors = _intent_processors((*_component_providers(data, account, reference), *providers))
        self.components = self.services + _runtime_projections(
            strategy_id=strategy_id,
            intents=intents,
        ) + provided_components((*_component_providers(data, account, reference), *providers)) + components
        register_components(self.views, self.components)

    def publish(self) -> None:
        publish_components(self.views, self.components)

    def on_event(self, event: RuntimeEnvelope) -> None:
        for service in self.services:
            service.on_event(event)
        for component in self.components[len(self.services) :]:
            component.on_event(event)
        publish_components(self.views, self.components, event=event)

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        if not intents:
            return
        for processor in self.intent_processors:
            processor.on_intents(intents, context, hook)
        publish_components(self.views, self.components, as_of=getattr(context, "now", None))


def _runtime_services(*services: MarketDataService | AccountService | None) -> tuple[MarketDataService | AccountService, ...]:
    return tuple(service for service in services if service is not None)


def _component_providers(*services: object | None) -> tuple[RuntimeComponentProvider, ...]:
    return tuple(
        service
        for service in services
        if service is not None and callable(getattr(service, "runtime_components", None))
    )


def _intent_processors(providers: tuple[RuntimeComponentProvider, ...]) -> tuple[RuntimeIntentProcessor, ...]:
    return tuple(
        provider
        for provider in providers
        if callable(getattr(provider, "on_intents", None))
    )


def _runtime_projections(
    *,
    strategy_id: str,
    intents: IntentJournal,
) -> tuple[RuntimeViewPublisher, ...]:
    return (
        RuntimeSystemProjection(strategy_id=strategy_id),
        SystemEventProjection(),
        RiskEventProjection(),
        IntentJournalProjection(strategy_id=strategy_id, intents=intents),
    )


__all__ = ["RuntimeServicePipeline"]
