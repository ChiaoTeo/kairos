from __future__ import annotations

from typing import Mapping

from kairospy.application.support.launch.application.configuration import ConfiguredPaper
from kairospy.application.support.runtime.domain.modes import RuntimeMode
from kairospy.application.support.runtime.domain.connections import DefaultConnectionManager
from kairospy.application.usecases.market.application.runtime import PaperMarketDataService, RuntimeIterableMarketEventSource
from kairospy.application.support.composition.application.accounts import PaperAccountResources
from kairospy.application.support.composition.application.integrations import integration_application, market_request_connections, market_stream_connections

from .common import ComposedLaunch, optional_default_text, reference_runtime


class PaperComposition:
    def compose(self, configured: ConfiguredPaper) -> ComposedLaunch:
        connections = DefaultConnectionManager()
        integration = integration_application()
        stream_connections = market_stream_connections(
            configured.feeds,
            mode_label="paper",
            application=integration,
        ) if configured.managed_market_feed_resolver and configured.source is None else {}
        request_connections = market_request_connections(
            configured.feeds,
            mode_label="paper",
            application=integration,
        ) if configured.managed_market_feed_resolver and configured.source is None else {}
        for connection_id, connection in stream_connections.items():
            connections.register(connection_id, connection, role="market_stream")
        for connection_id, connection in request_connections.items():
            connections.register(connection_id, connection, role="market_request")
        market_data = _market_data(configured)
        market_data.stream_connections = dict(stream_connections)
        if stream_connections:
            market_data.feed_resolver = None
        market_data.set_connection_manager(connections)
        resources = PaperAccountResources.from_configured(configured)
        from kairospy.application.support.runtime.application.launch.resources import TradingRuntimeResources
        launch_resources = TradingRuntimeResources(
            source=market_data,
            data=market_data,
            account=resources.account,
            reference=reference_runtime(
                configured.launch_directory,
                default_venue=configured.account_config.venue,
                default_market=_paper_default_market(configured.normalized_config),
            ),
            trading_execution=resources.execution,
            connection_scope=connections,
            market_stream_connections=stream_connections,
            market_request_connections=request_connections,
        )
        return ComposedLaunch(
            mode=RuntimeMode.PAPER,
            launch_id=configured.launch_id,
            strategy=configured.strategy,
            launch_directory=configured.launch_directory,
            normalized_config=configured.normalized_config,
            resources=launch_resources,
            lifecycle=None,
            build_result=lambda runtime: resources.build_result(configured, runtime),
        )


def _paper_default_market(config: Mapping[str, object]) -> str:
    market = config.get("market")
    if isinstance(market, Mapping):
        return optional_default_text(market.get("market")) or "spot"
    return "spot"


def _market_data(configured: ConfiguredPaper) -> PaperMarketDataService:
    if configured.source is not None:
        return PaperMarketDataService(
            RuntimeIterableMarketEventSource(configured.source),
            source_name=configured.source_name,
        )
    return PaperMarketDataService(
        feed_resolver=configured.market_feed_resolver,
        source_name=configured.source_name,
    )


__all__ = ["PaperComposition"]
