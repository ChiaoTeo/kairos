from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.infrastructure.integrations.domain import AccessScope, ProductFamily, TransportKind
from kairospy.domain.market import Bar
from kairospy.domain.reference import ReferenceCatalog
from .client import BinanceSpotRestClient
from kairospy.infrastructure.integrations.services.connections.connection import Connection
from kairospy.infrastructure.integrations.services.gateways.ccxt.driver import CcxtMarketDriver
from .operations import BinanceSpotMarketOperations
from kairospy.infrastructure.integrations.services.gateways.binance.spot.normalizers import BinanceSpotNormalizers


class BinanceSpotPublicRestConnection(Connection):
    def __init__(self, spec: IntegrationConnectionSpec) -> None:
        self.operations = BinanceSpotMarketOperations(BinanceSpotRestClient())
        self.normalizers = BinanceSpotNormalizers()
        self.historical_driver = CcxtMarketDriver()
        super().__init__(spec, components=())

    def bars(self, symbol: str, *, timeframe: str = "1m", since: datetime | None = None, until: datetime | None = None, limit: int = 1000) -> Iterable[Bar]:
        try:
            payload = self.historical_driver.ohlcv(symbol, timeframe=timeframe, since=_millis(since), limit=limit, until=_millis(until))
        except RuntimeError as error:
            if "requires the crypto extra" not in str(error):
                raise
            payload = self.operations.klines(symbol=symbol, interval=timeframe, limit=limit, start_time=_millis(since), end_time=_millis(until))
        return self.normalizers.bars(payload, symbol=symbol, timeframe=timeframe)

    def catalog(self, *, as_of: datetime, market: str | None = None) -> ReferenceCatalog:
        return self.normalizers.catalog(self.operations.exchange_info(), as_of=as_of)


class BinanceSpotPublicRestGateway:
    def open(self, spec: IntegrationConnectionSpec) -> BinanceSpotPublicRestConnection:
        if spec.product is not ProductFamily.SPOT or spec.access is not AccessScope.PUBLIC or spec.transport is not TransportKind.REST:
            raise ValueError("Binance Spot public REST gateway received an incompatible connection spec")
        return BinanceSpotPublicRestConnection(spec)


def _millis(value: object | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    return int(value)


__all__ = ["BinanceSpotPublicRestConnection", "BinanceSpotPublicRestGateway"]
