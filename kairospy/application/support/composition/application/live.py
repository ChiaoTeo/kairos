from __future__ import annotations

from kairospy.application.support.launch.application.configuration import ConfiguredLive
from kairospy.application.support.launch.application.results import live_result
from kairospy.application.support.composition.application.lifecycle import LiveConfiguredLifecycle
from kairospy.application.support.launch.domain.modes import RuntimeMode
from kairospy.application.actor.support.services.connections import IntegrationConnectionScope
from kairospy.application.usecases.market.application.runtime import build_live_market
from kairospy.application.system.application.business import SystemApplication
from kairospy.application.support.composition.application.accounts import LiveAccountResources
from kairospy.application.support.composition.application.integrations import integration_application, market_integration_runtime

from .common import ComposedLaunch, in_memory_message_bus, reference_runtime
from .runtime import compose_runtime_assembly


class LiveComposition:
    def compose(self, configured: ConfiguredLive) -> ComposedLaunch:
        connections = IntegrationConnectionScope()
        integration = integration_application()
        market_runtime = market_integration_runtime(connections, application=integration, mode=RuntimeMode.LIVE) if configured.managed_market_feed_resolver else None
        market_data = build_live_market(
            feed_resolver=None if configured.managed_market_feed_resolver else configured.market_feed_resolver,
            source_name=configured.source_name,
            integration_runtime=market_runtime,
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
            account=account_resources.account,
            reference=reference_runtime(
                configured.launch_directory,
                default_venue=configured.account_config.venue,
                default_market="spot",
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
