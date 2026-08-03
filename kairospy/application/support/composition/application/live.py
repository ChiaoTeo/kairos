from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kairospy.application.support.launch.application.configuration import ConfiguredLive
from kairospy.application.support.runtime.domain.modes import RuntimeMode
from kairospy.application.support.runtime.domain.connections import DefaultConnectionManager
from kairospy.application.usecases.market.application.runtime import LiveMarketDataService
from kairospy.application.usecases.account.application.runtime import LiveAccountService
from kairospy.application.support.composition.application.accounts import LiveAccountResources
from kairospy.application.support.composition.application.integrations import integration_application, market_request_connections, market_stream_connections
from kairospy.infrastructure.persistence.application.runtime_state import JsonLiveRuntimeStateStore

from .common import ComposedLaunch, reference_runtime


class LiveComposition:
    def compose(self, configured: ConfiguredLive) -> ComposedLaunch:
        connections = DefaultConnectionManager()
        integration = integration_application()
        stream_connections = market_stream_connections(
            configured.feeds, mode_label="live", application=integration,
        ) if configured.managed_market_feed_resolver else {}
        request_connections = market_request_connections(
            configured.feeds, mode_label="live", application=integration,
        ) if configured.managed_market_feed_resolver else {}
        for connection_id, connection in stream_connections.items():
            connections.register(connection_id, connection, role="market_stream")
        for connection_id, connection in request_connections.items():
            connections.register(connection_id, connection, role="market_request")
        market_data = LiveMarketDataService(
            feed_resolver=None if configured.managed_market_feed_resolver else configured.market_feed_resolver,
            source_name=configured.source_name,
            stream_connections=stream_connections,
        )
        market_data.set_connection_manager(connections)
        account_resources = LiveAccountResources.from_configured(configured, integration_application=integration)
        for connection_id, connection in account_resources.integration_connections.items():
            connections.register(connection_id, connection, role="account_or_execution")
        if account_resources.integration_connections:
            account_resources.account.connections = connections
        else:
            account_resources.account.set_connection_manager(connections)
        from kairospy.application.support.runtime.application.launch.resources import TradingRuntimeResources
        launch_resources = TradingRuntimeResources(
            source=market_data,
            data=market_data,
            account=account_resources.account,
            reference=reference_runtime(
                configured.launch_directory,
                default_venue=configured.account_config.venue,
                default_market="spot",
            ),
            trading_execution=account_resources.execution,
            connection_scope=connections,
            market_stream_connections=stream_connections,
            market_request_connections=request_connections,
            account_connections={key: value for key, value in account_resources.integration_connections.items() if ".account." in key},
            execution_connections={key: value for key, value in account_resources.integration_connections.items() if ".execution." in key},
        )
        return ComposedLaunch(
            mode=RuntimeMode.LIVE,
            launch_id=configured.launch_id,
            strategy=configured.strategy,
            launch_directory=configured.launch_directory,
            normalized_config=configured.normalized_config,
            resources=launch_resources,
            lifecycle=LiveConfiguredLifecycle(configured.state_path, account=account_resources.account, coordinator=account_resources.coordinator),
            build_result=lambda runtime: account_resources.build_result(configured, runtime),
        )


class LiveConfiguredLifecycle:
    def __init__(self, state_path: Path, *, account: LiveAccountService, coordinator: object) -> None:
        self.account = account
        self.coordinator = coordinator
        self.state_store = JsonLiveRuntimeStateStore(state_path)

    def prepare(self) -> None:
        snapshot = self.state_store.load()
        if snapshot is not None:
            snapshot.restore_execution_into(self.coordinator)
            self.account.private_stream_state.restore_checkpoint(snapshot.private_stream)
        self.account.refresh()

    def complete(self) -> None:
        self.state_store.save(self.coordinator, self.account.private_stream_state.checkpoint())


__all__ = ["LiveComposition", "LiveConfiguredLifecycle"]
