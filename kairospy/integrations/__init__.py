from __future__ import annotations

from .events import BrokerConnected, BrokerDisconnected, IntegrationPayload

__all__ = [
    "BrokerConnected",
    "BrokerDisconnected",
    "IntegrationPayload",
    "HyperliquidExecutionGatewayRequired",
    "LiveMarketDataRequest",
    "LiveMarketDataSourceConfig",
    "LiveMarketEventSourceBinding",
    "LiveProviderPorts",
    "build_live_market_data_event_source",
    "build_live_market_event_source",
    "build_live_provider_ports",
    "parse_account_ref",
    "resolve_live_market_data_source_config",
]

_LIVE_PORT_EXPORTS = {
    "HyperliquidExecutionGatewayRequired",
    "LiveMarketEventSourceBinding",
    "LiveProviderPorts",
    "build_live_market_event_source",
    "build_live_provider_ports",
    "parse_account_ref",
}

_LIVE_MARKET_DATA_EXPORTS = {
    "LiveMarketDataRequest",
    "LiveMarketDataSourceConfig",
    "build_live_market_data_event_source",
    "resolve_live_market_data_source_config",
}


def __getattr__(name: str) -> object:
    if name in _LIVE_PORT_EXPORTS:
        from . import live_ports

        return getattr(live_ports, name)
    if name in _LIVE_MARKET_DATA_EXPORTS:
        from . import live_market_data

        return getattr(live_market_data, name)
    raise AttributeError(name)
