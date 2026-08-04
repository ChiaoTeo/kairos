from __future__ import annotations

import os

from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.application.usecases.market.application.integration import MarketFeedSubscriptionRequest
from kairospy.infrastructure.integrations.domain import ProductFamily
from kairospy.infrastructure.integrations.services.connections.connection import Connection
from kairospy.infrastructure.integrations.services.credentials import credential_value
from kairospy.infrastructure.integrations.services.drivers.websocket import WebSocketDriver
from .stream import (
    MassiveOptionsMarketStream,
    MassiveStockMarketStream,
)
from kairospy.infrastructure.integrations.services.gateways.massive.normalizers import MassiveStockNormalizers


class MassiveStockMarketStreamConnection(Connection):
    def __init__(self, spec: IntegrationConnectionSpec, *, api_key: str | None = None, driver: WebSocketDriver | None = None) -> None:
        resolved_key = api_key or credential_value(spec.credential.id if spec.credential else None, "api_key") or os.getenv("MASSIVE_API_KEY")
        self.stream = MassiveStockMarketStream(api_key=resolved_key, driver=driver or WebSocketDriver())
        self.normalizers = MassiveStockNormalizers()
        self._subscriptions: dict[str, MassiveStockRemoteSubscription] = {}
        super().__init__(spec, components=(self.stream,))

    async def subscribe(self, request: MarketFeedSubscriptionRequest) -> "MassiveStockRemoteSubscription":
        channel = _market_channel(request.selector)
        if channel not in {"ticker", "trade"}:
            raise ValueError(f"Massive stock stream does not support market channel: {channel}")
        event_code = "Q" if channel == "ticker" else "T"
        subscription_id = await self.stream.subscribe(str(request.market.source_symbol), {event_code})
        remote = MassiveStockRemoteSubscription(
            subscription_id=subscription_id,
            stream=self.stream,
            market=request.market,
            channel=channel,
            normalizers=self.normalizers,
        )
        self._subscriptions[subscription_id] = remote
        return remote

    async def unsubscribe(self, subscription_id: str) -> None:
        self._subscriptions.pop(subscription_id, None)
        await self.stream.unsubscribe(subscription_id)


class MassiveOptionsMarketStreamConnection(MassiveStockMarketStreamConnection):
    """Massive options stream; Q/T payloads share the stock canonical mapping."""

    def __init__(self, spec: IntegrationConnectionSpec, *, api_key: str | None = None, driver: WebSocketDriver | None = None) -> None:
        resolved_key = api_key or credential_value(spec.credential.id if spec.credential else None, "api_key") or os.getenv("MASSIVE_API_KEY")
        self.stream = MassiveOptionsMarketStream(api_key=resolved_key, driver=driver or WebSocketDriver())
        self.normalizers = MassiveStockNormalizers()
        self._subscriptions: dict[str, MassiveStockRemoteSubscription] = {}
        Connection.__init__(self, spec, components=(self.stream,))


class MassiveReferenceConnection(Connection):
    """Provider identity connection used for non-stream reference access."""

    def __init__(self, spec: IntegrationConnectionSpec) -> None:
        if spec.product is not None:
            raise ValueError("Massive reference connection does not select a product")
        super().__init__(spec, components=())


class MassiveStocksGateway:
    def open(self, spec: IntegrationConnectionSpec) -> MassiveStockMarketStreamConnection:
        if spec.product is not ProductFamily.EQUITY:
            raise ValueError("Massive stocks gateway requires the equity product")
        return MassiveStockMarketStreamConnection(spec)


class MassiveOptionsGateway:
    def open(self, spec: IntegrationConnectionSpec) -> MassiveOptionsMarketStreamConnection:
        if spec.product is not ProductFamily.OPTIONS:
            raise ValueError("Massive options gateway requires the options product")
        return MassiveOptionsMarketStreamConnection(spec)


class MassiveReferenceGateway:
    def open(self, spec: IntegrationConnectionSpec) -> MassiveReferenceConnection:
        return MassiveReferenceConnection(spec)


class MassiveStockRemoteSubscription:
    def __init__(self, *, subscription_id: str, stream: MassiveStockMarketStream, market: object, channel: str, normalizers: MassiveStockNormalizers) -> None:
        self.subscription_id = subscription_id
        self.stream = stream
        self.market = market
        self.channel = channel
        self.normalizers = normalizers

    def events(self):
        return self._events()

    async def close(self) -> None:
        await self.stream.unsubscribe(self.subscription_id)

    async def _events(self):
        async for payload in self.stream.events(self.subscription_id):
            yield self.normalizers.market_domain_event(payload, market=self.market, channel=self.channel)


def _market_channel(selector: object) -> str:
    model = getattr(selector, "model", selector)
    name = getattr(model, "__name__", str(model)).lower()
    if "quote" in name:
        return "ticker"
    if "trade" in name:
        return "trade"
    return name


__all__ = [
    "MassiveReferenceConnection",
    "MassiveReferenceGateway",
    "MassiveStocksGateway",
    "MassiveOptionsGateway",
    "MassiveOptionsMarketStreamConnection",
    "MassiveStockMarketStreamConnection",
]
