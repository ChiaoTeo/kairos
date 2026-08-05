from __future__ import annotations

from kairospy.application.support.launch.application.configuration import ConfiguredLive
from kairospy.application.support.launch.application.results import live_result
from kairospy.application.support.composition.application.lifecycle import LiveConfiguredLifecycle
from kairospy.application.support.launch.domain.modes import RuntimeMode
from kairospy.application.actor.support.services.connections import IntegrationConnectionScope
from kairospy.application.usecases.market.application.runtime import build_live_market
from kairospy.application.system.application.business import SystemApplication
from kairospy.application.support.composition.application.accounts import LiveAccountResources
from kairospy.application.support.composition.application.integrations import connect_massive_options, integration_application, market_integration_runtime
from kairospy.infrastructure.integrations.application.connections import TransportKind
from kairospy.infrastructure.persistence.application.market_data import DataStore

from .common import ComposedLaunch, in_memory_message_bus, reference_runtime
from .runtime import compose_runtime_assembly


class LiveComposition:
    def compose(self, configured: ConfiguredLive) -> ComposedLaunch:
        connections = IntegrationConnectionScope()
        integration = integration_application()
        market_runtime = (
            market_integration_runtime(
                connections,
                application=integration,
                mode=RuntimeMode.LIVE,
                feed_configs=configured.feeds,
            )
            if configured.managed_market_feed_resolver
            else None
        )
        market_data = build_live_market(
            feed_resolver=None if configured.managed_market_feed_resolver else configured.market_feed_resolver,
            source_name=configured.source_name,
            integration_runtime=market_runtime,
            warmup_client_factory=_massive_historical_factory(configured.feeds),
        )
        market_data.set_connection_manager(connections)
        account_resources = LiveAccountResources.from_configured(configured, integration_application=integration)
        for connection_id, connection in account_resources.integration_connections.items():
            connections.register(connection_id, connection, role="account_or_execution")
        if account_resources.integration_connections:
            account_resources.account.connections = connections
        else:
            account_resources.account.set_connection_manager(connections)
        from kairospy.application.system.application.resources import TradingSystemResources
        launch_resources = TradingSystemResources(
            business=SystemApplication(),
            data=market_data,
            market_store=DataStore(".kairos/data"),
            account=account_resources.account,
            reference=reference_runtime(
                configured.launch_directory,
                default_venue=configured.account_config.venue,
                default_market=_live_default_market(configured.normalized_config),
                credential=_massive_credential(configured.feeds),
            ),
            trading_execution=account_resources.execution,
            connection_scope=connections,
            message_bus=in_memory_message_bus(),
            assembly=compose_runtime_assembly(),
        )
        return ComposedLaunch(
            mode=RuntimeMode.LIVE,
            launch_id=configured.launch_id,
            strategy=configured.strategy,
            launch_directory=configured.launch_directory,
            normalized_config=configured.normalized_config,
            resources=launch_resources,
            lifecycle=LiveConfiguredLifecycle(configured.state_path, account=account_resources.account, coordinator=account_resources.coordinator),
            build_result=lambda runtime: live_result(configured, account_resources, runtime),
        )


__all__ = ["LiveComposition"]


def _live_default_market(config: object) -> str:
    if isinstance(config, dict):
        market = config.get("market")
        if isinstance(market, dict):
            value = market.get("market")
            if value is not None and str(value).strip():
                return str(value).strip()
    return "spot"


def _massive_credential(feeds: object) -> str | None:
    if isinstance(feeds, dict):
        values = feeds.values()
    else:
        values = tuple(getattr(feeds, "values", lambda: ())())
    for feed in values:
        raw = getattr(feed, "values", None)
        if isinstance(raw, dict) and str(raw.get("venue", "")).lower() == "massive":
            credential = raw.get("credential")
            return None if credential is None else str(credential)
    return None


def _massive_historical_factory(feeds: object):
    credential = _massive_credential(feeds)
    if credential is None:
        return None

    def factory(spec):
        if str(getattr(spec, "venue", "")).lower() != "massive":
            return None
        return connect_massive_options(
            f"live.warmup.massive.{spec.market}",
            credential=credential,
            transport=TransportKind.REST,
            mode=RuntimeMode.LIVE,
        )

    return factory
