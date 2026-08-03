from __future__ import annotations

from datetime import datetime

from kairospy.domain.market import Quote
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.infrastructure.integrations.services.clients.binance_equity import BinanceEquityRestClient
from kairospy.infrastructure.integrations.services.connections.base import ConnectionService
from kairospy.infrastructure.integrations.services.operations.binance_equity import BinanceEquityMarketOperations
from kairospy.infrastructure.integrations.services.translators.binance_equity import BinanceEquityPayloadTranslator
from kairospy.infrastructure.integrations.services.connection_services.binance_equity_stream import BinanceEquityPollingConnection


class BinanceEquityPublicRestConnection(ConnectionService):
    def __init__(self, spec: IntegrationConnectionSpec) -> None:
        client = BinanceEquityRestClient(credential_id=spec.credential.id if spec.credential else None)
        self.operations = BinanceEquityMarketOperations(client)
        self.translator = BinanceEquityPayloadTranslator()
        super().__init__(spec, components=())

    def latest_quote(self, symbol: str) -> Quote | None:
        return self.translator.latest_quote(self.operations.latest_quote(symbol))

    def catalog(self, *, as_of: datetime, market: str | None = None):
        return self.translator.catalog(self.operations.exchange_info(symbol=market), as_of=as_of)


def BinanceEquityConnectionService(spec: IntegrationConnectionSpec) -> ConnectionService:
    if spec.product.value != "equity":
        raise ValueError("Binance Equity connection requires the equity product")
    if spec.transport.value == "websocket_market_stream":
        return BinanceEquityPollingConnection(spec)
    if spec.transport.value == "rest":
        return BinanceEquityPublicRestConnection(spec)
    raise ValueError(f"unsupported Binance Equity link: {spec.access}/{spec.transport}")


__all__ = ["BinanceEquityConnectionService", "BinanceEquityPublicRestConnection"]
