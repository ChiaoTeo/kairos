from __future__ import annotations

from datetime import datetime

from kairospy.domain.market import Quote
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.infrastructure.integrations.domain import AccessScope, AssetType, ProductFamily, TransportKind
from .client import BinanceEquityRestClient
from kairospy.infrastructure.integrations.services.connections.connection import Connection
from .operations import BinanceEquityMarketOperations
from kairospy.infrastructure.integrations.services.gateways.binance.equity.normalizers import BinanceEquityNormalizers
from kairospy.infrastructure.integrations.services.gateways.binance.equity.public_stream import BinanceEquityPollingConnection
from kairospy.application.usecases.reference.application.requests import ReferenceCatalogRequest


class BinanceEquityPublicRestConnection(Connection):
    def __init__(self, spec: IntegrationConnectionSpec) -> None:
        client = BinanceEquityRestClient(credential_id=spec.credential.id if spec.credential else None)
        self.operations = BinanceEquityMarketOperations(client)
        self.normalizers = BinanceEquityNormalizers()
        super().__init__(spec, components=())

    def latest_quote(self, symbol: str) -> Quote | None:
        return self.normalizers.latest_quote(self.operations.latest_quote(symbol))

    def catalog(self, request: ReferenceCatalogRequest):
        return self.normalizers.catalog(
            self.operations.exchange_info(symbol=request.market),
            as_of=request.as_of,
        )


class BinanceEquityPublicRestGateway:
    def open(self, spec: IntegrationConnectionSpec) -> BinanceEquityPublicRestConnection:
        _validate_public_equity(spec, TransportKind.REST)
        return BinanceEquityPublicRestConnection(spec)


class BinanceEquityPublicStreamGateway:
    def open(self, spec: IntegrationConnectionSpec) -> BinanceEquityPollingConnection:
        _validate_public_equity(spec, TransportKind.MARKET_STREAM)
        return BinanceEquityPollingConnection(spec)


def _validate_public_equity(spec: IntegrationConnectionSpec, transport: TransportKind) -> None:
    if spec.product is not ProductFamily.SPOT or spec.asset_type is not AssetType.EQUITY or spec.access is not AccessScope.PUBLIC or spec.transport is not transport:
        raise ValueError(f"Binance Equity gateway requires {transport.value} equity transport")


__all__ = [
    "BinanceEquityPublicRestConnection",
    "BinanceEquityPublicRestGateway",
    "BinanceEquityPublicStreamGateway",
]
