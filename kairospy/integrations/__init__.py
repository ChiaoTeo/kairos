from __future__ import annotations

from .events import BrokerConnected, BrokerDisconnected, IntegrationPayload

__all__ = [
    "BrokerConnected",
    "BrokerDisconnected",
    "IntegrationPayload",
    "LiveMarketEventSourceBinding",
    "LiveProviderPorts",
    "build_live_market_event_source",
    "build_live_provider_ports",
    "parse_account_ref",
]

_LIVE_PORT_EXPORTS = {
    "LiveMarketEventSourceBinding",
    "LiveProviderPorts",
    "build_live_market_event_source",
    "build_live_provider_ports",
    "parse_account_ref",
}


def __getattr__(name: str) -> object:
    if name in _LIVE_PORT_EXPORTS:
        from . import live_ports

        return getattr(live_ports, name)
    raise AttributeError(name)
