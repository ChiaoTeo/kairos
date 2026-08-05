from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from datetime import datetime

from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.application.usecases.market.application.integration import MarketFeedSubscriptionRequest
from kairospy.application.usecases.market.application.requests import MarketOptions, MarketTime
from kairospy.infrastructure.integrations.domain import AssetType, ProductFamily
from kairospy.infrastructure.integrations.services.connections.connection import Connection
from kairospy.infrastructure.integrations.services.credentials import credential_value
from kairospy.infrastructure.integrations.services.drivers.websocket import WebSocketDriver
from .stream import (
    MassiveOptionsMarketStream,
    MassiveStockMarketStream,
)
from .client import MassiveStocksRestClient
from kairospy.domain.market import Bar
from kairospy.application.usecases.reference.application.builders import catalog_from_market_snapshot
from kairospy.application.usecases.reference.application.requests import ReferenceCatalogRequest
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


class MassiveOptionsMarketDataConnection(Connection):
    """Massive REST market-data capability for historical option bars."""

    def __init__(self, spec: IntegrationConnectionSpec, *, api_key: str | None = None) -> None:
        resolved_key = api_key or credential_value(spec.credential.id if spec.credential else None, "api_key") or os.getenv("MASSIVE_API_KEY")
        self.client = MassiveStocksRestClient(api_key=resolved_key)
        super().__init__(spec, components=())

    def bars(
        self,
        symbol: str,
        *,
        timeframe: str = "1m",
        since: datetime | str | None = None,
        until: datetime | str | None = None,
        limit: int = 1000,
        adapter_options: MarketOptions | None = None,
    ) -> Iterable[Bar]:
        return self.client.bars(
            symbol,
            timeframe=timeframe,
            since=since,
            until=until,
            limit=limit,
            market="option",
            adapter_options=adapter_options,
        )

    def latest_quote(self, symbol: str) -> None:
        # The live NBBO is provided by the options WebSocket connection.
        return None


class MassiveReferenceConnection(Connection):
    """Provider identity connection used for non-stream reference access."""

    def __init__(self, spec: IntegrationConnectionSpec) -> None:
        if spec.product is not None:
            raise ValueError("Massive reference connection does not select a product")
        self.client = MassiveStocksRestClient(
            credential_id=spec.credential.id if spec.credential else None,
        )
        super().__init__(spec, components=())

    def catalog(self, request: ReferenceCatalogRequest):
        if str(request.market or "").lower() not in {"option", "options"}:
            raise ValueError("Massive reference catalog currently requires market=option")
        underlying = request.underlying or os.getenv("MASSIVE_OPTION_UNDERLYING") or "SPY"
        rows = []
        for item in self.client.option_contracts(underlying, as_of=request.as_of.date().isoformat()):
            rows.append({
                "venue": "massive",
                "market": "option",
                "source_symbol": item.get("ticker"),
                "underlying_instrument_id": f"instrument:equity:{underlying.lower()}",
                "expiry": item.get("expiration_date"),
                "strike_price": item.get("strike_price"),
                "contract_type": item.get("contract_type"),
                "shares_per_contract": item.get("shares_per_contract", 100),
                "active": item.get("active", True),
                "raw": item,
            })
        return catalog_from_market_snapshot(rows, effective_from=request.as_of)


class MassiveStocksGateway:
    def open(self, spec: IntegrationConnectionSpec) -> MassiveStockMarketStreamConnection:
        if spec.product is not ProductFamily.SPOT or spec.asset_type is not AssetType.EQUITY:
            raise ValueError("Massive stocks gateway requires the equity product")
        return MassiveStockMarketStreamConnection(spec)


class MassiveOptionsGateway:
    def open(self, spec: IntegrationConnectionSpec) -> MassiveOptionsMarketStreamConnection:
        if spec.product is not ProductFamily.OPTIONS:
            raise ValueError("Massive options gateway requires the options product")
        if spec.transport.value == "rest":
            return MassiveOptionsMarketDataConnection(spec)  # type: ignore[return-value]
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
    "MassiveOptionsMarketDataConnection",
    "MassiveStockMarketStreamConnection",
]
