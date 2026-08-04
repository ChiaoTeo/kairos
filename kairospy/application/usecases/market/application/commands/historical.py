from __future__ import annotations

from collections.abc import Iterable

from kairospy.application.usecases.market.application.component import MarketApplication
from kairospy.application.usecases.market.application.data import MarketDataSpec
from .resources import DriverName, ExchangeName, MarketCommandResources, StorageFormat


class _HistoricalConnectionSource:
    """System adapter translating a selected integration connection to the market source contract."""

    def __init__(self, connection: object) -> None:
        self._connection = connection

    def fetch(self, spec: MarketDataSpec) -> Iterable[object]:
        symbol = spec.symbol
        if spec.kind == "ohlcv":
            fetch = getattr(self._connection, "bars", None)
            if not callable(fetch):
                raise ValueError("selected market connection does not provide historical bars")
            return fetch(
                symbol,
                timeframe=spec.timeframe or "1m",
                since=spec.start,
                until=spec.end,
                limit=spec.limit or 1000,
            )
        if spec.kind == "funding_rate":
            fetch = getattr(self._connection, "funding_rates", None)
            if not callable(fetch):
                raise ValueError("selected market connection does not provide historical funding rates")
            return fetch(
                symbol,
                since=spec.start,
                until=spec.end,
                limit=spec.limit or 1000,
            )
        raise ValueError(f"unsupported historical data kind: {spec.kind}")


class MarketHistoricalCommandService:
    """System command for one historical market-data download."""

    def __init__(self, resources: MarketCommandResources) -> None:
        self._resources = resources

    def download(
        self,
        *,
        symbol: str,
        dataset: str | None,
        root: str | None,
        storage_format: StorageFormat | None,
        exchange_name: ExchangeName,
        driver_name: DriverName,
        market: str,
        kind: str,
        timeframe: str,
        start: str | None,
        end: str | None,
        limit: int,
        mode: str,
    ) -> str:
        spec = MarketDataSpec(
            symbol=symbol,
            kind=kind,
            venue=exchange_name.value,
            market=market,
            timeframe=timeframe,
            start=start,
            end=end,
            limit=limit,
            dataset=dataset,
        )
        service = MarketApplication(
            store=self._resources.data_store(root, storage_format),
            resolver=None,
        )
        source = _HistoricalConnectionSource(self._resources.public_market_access(exchange_name, driver_name))
        return str(service.ingestion.persist_historical(spec, source.fetch(spec), mode=mode))


__all__ = ["MarketHistoricalCommandService"]
