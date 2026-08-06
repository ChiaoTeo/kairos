from __future__ import annotations

from typing import Mapping

from kairospy.application.support.launch.application.configuration import ConfiguredPaper
from kairospy.application.support.launch.application.results import paper_result
from kairospy.application.support.launch.domain.modes import RuntimeMode
from kairospy.application.actor.support.services.connections import IntegrationConnectionScope
from kairospy.application.usecases.market.application.runtime import PaperMarketDataService, RuntimeIterableMarketEventSource, build_paper_market
from kairospy.application.system.application.business import SystemApplication
from kairospy.application.support.composition.application.accounts import PaperAccountResources
from kairospy.application.support.composition.application.integrations import connect_massive_options, integration_application, market_integration_runtime
from kairospy.infrastructure.integrations.application.connections import TransportKind
from kairospy.infrastructure.persistence.application.market_data import DataStore

from .common import ComposedLaunch, in_memory_message_bus, optional_default_text, reference_runtime, reference_underlyings
from .runtime import compose_runtime_assembly
from .notifications import build_notification_application, notification_runtime_settings


class PaperComposition:
    def compose(self, configured: ConfiguredPaper) -> ComposedLaunch:
        connections = IntegrationConnectionScope()
        integration = integration_application()
        market_runtime = None
        if configured.managed_market_feed_resolver and configured.source is None:
            market_runtime = market_integration_runtime(
                connections,
                application=integration,
                mode=RuntimeMode.PAPER,
                feed_configs=configured.feeds,
            )
        market_data = _market_data(configured)
        market_data.integration_runtime = market_runtime
        if market_runtime is not None:
            market_data.feed_resolver = None
        market_data.set_connection_manager(connections)
        resources = PaperAccountResources.from_configured(configured)
        from kairospy.application.system.application.resources import TradingSystemResources
        launch_resources = TradingSystemResources(
            business=SystemApplication(),
            data=market_data,
            market_store=DataStore(".kairos/data"),
            account=resources.account,
            reference=reference_runtime(
                configured.launch_directory,
                default_venue=configured.account_config.venue,
                default_market=_paper_default_market(configured.normalized_config),
                underlyings=reference_underlyings(configured.normalized_config),
                credential=_massive_credential(configured.feeds),
            ),
            trading_execution=resources.execution,
            connection_scope=connections,
            message_bus=in_memory_message_bus(),
            notifications=build_notification_application(configured.normalized_config),
            notification_settings=notification_runtime_settings(configured.normalized_config),
            assembly=compose_runtime_assembly(),
        )
        return ComposedLaunch(
            mode=RuntimeMode.PAPER,
            launch_id=configured.launch_id,
            strategy=configured.strategy,
            launch_directory=configured.launch_directory,
            normalized_config=configured.normalized_config,
            resources=launch_resources,
            lifecycle=None,
            build_result=lambda runtime: paper_result(configured, resources, runtime),
        )


def _paper_default_market(config: Mapping[str, object]) -> str:
    market = config.get("market")
    if isinstance(market, Mapping):
        return optional_default_text(market.get("market")) or "spot"
    return "spot"


def _massive_credential(feeds: Mapping[str, object]) -> str | None:
    for feed in feeds.values():
        values = getattr(feed, "values", None)
        if isinstance(values, Mapping) and str(values.get("venue", "")).lower() == "massive":
            credential = values.get("credential")
            return None if credential is None else str(credential)
    return None


def _market_data(configured: ConfiguredPaper) -> PaperMarketDataService:
    if configured.source is not None:
        return build_paper_market(
            RuntimeIterableMarketEventSource(configured.source),
            source_name=configured.source_name,
            warmup_client_factory=_massive_historical_factory(configured.feeds),
        )
    return build_paper_market(
        feed_resolver=configured.market_feed_resolver,
        source_name=configured.source_name,
        warmup_client_factory=_massive_historical_factory(configured.feeds),
    )


def _massive_historical_factory(feeds: Mapping[str, object]):
    credential = _massive_credential(feeds)
    if credential is None:
        return None

    def factory(spec):
        if str(getattr(spec, "venue", "")).lower() != "massive":
            return None
        return connect_massive_options(
            f"paper.warmup.massive.{spec.market}",
            credential=credential,
            transport=TransportKind.REST,
            mode=RuntimeMode.PAPER,
        )

    return factory


__all__ = ["PaperComposition"]
