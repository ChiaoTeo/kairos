from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.domain.market import Bar
from kairospy.domain.reference import ReferenceCatalog
from kairospy.infrastructure.integrations.services.clients.binance_spot import BinanceSpotRestClient
from kairospy.infrastructure.integrations.services.connections.base import ConnectionService
from kairospy.infrastructure.integrations.services.drivers.ccxt_market import CcxtMarketDriver
from kairospy.infrastructure.integrations.services.operations.binance_spot import BinanceSpotMarketOperations
from kairospy.infrastructure.integrations.services.translators.binance_spot import BinanceSpotPayloadTranslator


class BinanceSpotPublicRestConnection(ConnectionService):
    def __init__(self, spec: IntegrationConnectionSpec) -> None:
        self.operations = BinanceSpotMarketOperations(BinanceSpotRestClient())
        self.translator = BinanceSpotPayloadTranslator()
        self.historical_driver = CcxtMarketDriver()
        super().__init__(spec, components=())

    def bars(self, symbol: str, *, timeframe: str = "1m", since: datetime | None = None, until: datetime | None = None, limit: int = 1000) -> Iterable[Bar]:
        try:
            payload = self.historical_driver.ohlcv(symbol, timeframe=timeframe, since=_millis(since), limit=limit, until=_millis(until))
        except RuntimeError as error:
            if "requires the crypto extra" not in str(error):
                raise
            payload = self.operations.klines(symbol=symbol, interval=timeframe, limit=limit, start_time=_millis(since), end_time=_millis(until))
        return self.translator.bars(payload, symbol=symbol, timeframe=timeframe)

    def catalog(self, *, as_of: datetime, market: str | None = None) -> ReferenceCatalog:
        return self.translator.catalog(self.operations.exchange_info(), as_of=as_of)


def _millis(value: object | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    return int(value)


__all__ = ["BinanceSpotPublicRestConnection"]
