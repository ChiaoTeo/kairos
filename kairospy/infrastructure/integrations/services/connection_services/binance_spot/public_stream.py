from __future__ import annotations

from uuid import uuid4

from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.infrastructure.integrations.application.market import ConnectionMarketSubscriptionRequest
from kairospy.infrastructure.integrations.services.connections.base import ConnectionService
from kairospy.infrastructure.integrations.services.streams.binance_spot import BinanceSpotMarketStream
from kairospy.infrastructure.integrations.services.translators.binance_spot import BinanceSpotPayloadTranslator


class BinanceSpotPublicStreamConnection(ConnectionService):
    def __init__(self, spec: IntegrationConnectionSpec) -> None:
        self.stream = BinanceSpotMarketStream()
        self.translator = BinanceSpotPayloadTranslator()
        self._subscriptions: dict[str, BinanceSpotRemoteSubscription] = {}
        super().__init__(spec, components=(self.stream,))

    async def subscribe(self, request: ConnectionMarketSubscriptionRequest) -> "BinanceSpotRemoteSubscription":
        channel = _market_channel(request.selector)
        if channel not in {"ticker", "trade", "orderbook"}:
            raise ValueError(f"Binance Spot does not support market stream channel: {channel}")
        remote = BinanceSpotRemoteSubscription(
            subscription_id=f"binance-spot-{uuid4().hex}",
            stream=self.stream,
            symbol=str(request.market.source_symbol),
            channel=channel,
            stream_channel="depth" if channel == "orderbook" else channel,
            market=request.market,
            translator=self.translator,
        )
        self._subscriptions[remote.subscription_id] = remote
        return remote

    async def unsubscribe(self, subscription_id: str) -> None:
        remote = self._subscriptions.pop(subscription_id, None)
        if remote is not None:
            await remote.close()


class BinanceSpotRemoteSubscription:
    def __init__(self, subscription_id: str, stream: BinanceSpotMarketStream, symbol: str, channel: str, stream_channel: str, market: object, translator: BinanceSpotPayloadTranslator) -> None:
        self.subscription_id = subscription_id
        self.stream = stream
        self.symbol = symbol
        self.channel = channel
        self.stream_channel = stream_channel
        self.market = market
        self.translator = translator

    def events(self):
        return self._events()

    async def close(self) -> None:
        await self.stream.stop()

    async def _events(self):
        async for payload in self.stream.events(self.symbol, self.stream_channel):
            yield self.translator.market_domain_event(payload, market=self.market, channel=self.channel)


def _market_channel(selector: object) -> str:
    model = getattr(selector, "model", selector)
    name = getattr(model, "__name__", str(model)).lower()
    if "quote" in name:
        return "ticker"
    if "orderbook" in name:
        return "orderbook"
    if "trade" in name:
        return "trade"
    return name


__all__ = ["BinanceSpotPublicStreamConnection", "BinanceSpotRemoteSubscription"]
