from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Mapping
from uuid import uuid4

from kairospy.domain.market import MarketEvent, Quote
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.infrastructure.integrations.application.market import ConnectionMarketSubscriptionRequest
from kairospy.infrastructure.integrations.services.clients.binance_equity import BinanceEquityRestClient
from kairospy.infrastructure.integrations.services.connections.base import ConnectionService
from kairospy.infrastructure.integrations.services.operations.binance_equity import BinanceEquityMarketOperations
from kairospy.infrastructure.integrations.services.translators.binance_equity import BinanceEquityPayloadTranslator


class BinanceEquityPollingConnection(ConnectionService):
    """Market-stream application port backed by Binance's quote REST endpoint.

    Binance Stocks Trading exposes latest quotes through REST rather than a
    public market WebSocket.  Polling is kept behind the stream application
    port so strategies and the market usecase remain transport-independent.
    """

    def __init__(self, spec: IntegrationConnectionSpec, *, client: BinanceEquityRestClient | None = None) -> None:
        self.operations = BinanceEquityMarketOperations(client or BinanceEquityRestClient(credential_id=spec.credential.id if spec.credential else None))
        self.translator = BinanceEquityPayloadTranslator()
        self._subscriptions: dict[str, BinanceEquityQuoteSubscription] = {}
        super().__init__(spec, components=())

    async def subscribe(self, request: ConnectionMarketSubscriptionRequest) -> "BinanceEquityQuoteSubscription":
        if str(request.market.market).lower() != "equity":
            raise ValueError("Binance Equity polling requires the equity market")
        if request.selector.model is not Quote:
            raise ValueError("Binance Equity polling only supports Quote subscriptions")
        poll_seconds = _poll_seconds(request.params)
        remote = BinanceEquityQuoteSubscription(
            subscription_id=f"binance-equity-{uuid4().hex}",
            symbol=str(request.market.source_symbol),
            market=request.market,
            operations=self.operations,
            translator=self.translator,
            poll_seconds=poll_seconds,
        )
        self._subscriptions[remote.subscription_id] = remote
        return remote

    async def unsubscribe(self, subscription_id: str) -> None:
        remote = self._subscriptions.pop(subscription_id, None)
        if remote is not None:
            await remote.close()


class BinanceEquityQuoteSubscription:
    def __init__(self, *, subscription_id: str, symbol: str, market: object, operations: BinanceEquityMarketOperations, translator: BinanceEquityPayloadTranslator, poll_seconds: float) -> None:
        self.subscription_id = subscription_id
        self.symbol = symbol
        self.market = market
        self.operations = operations
        self.translator = translator
        self.poll_seconds = poll_seconds
        self._closed = asyncio.Event()

    def events(self):
        return self._events()

    async def close(self) -> None:
        self._closed.set()

    async def _events(self):
        while not self._closed.is_set():
            observed_at = datetime.now(timezone.utc)
            payload = self.operations.latest_quote(symbol=self.symbol)
            quote = self.translator.latest_quote(payload, market=self.market, observed_at=observed_at)  # type: ignore[arg-type]
            if quote is not None:
                yield MarketEvent(
                    subject=_market_subject(self.market),
                    observed_at=observed_at,
                    available_at=observed_at,
                    value=quote,
                    source="binance",
                    metadata={"symbol": self.symbol, "transport": "rest_polling"},
                )
            try:
                await asyncio.wait_for(self._closed.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                pass


def _market_subject(market: object):
    from kairospy.domain.market import MarketSubject

    return MarketSubject("market", getattr(market, "market_id"))


def _poll_seconds(params: Mapping[str, object]) -> float:
    value = params.get("poll_seconds", 5.0)
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Binance Equity poll_seconds must be a positive number") from error
    if result <= 0:
        raise ValueError("Binance Equity poll_seconds must be a positive number")
    return result


__all__ = ["BinanceEquityPollingConnection", "BinanceEquityQuoteSubscription"]
