from __future__ import annotations

from typing import Callable

from kairospy.application.usecases.market.application.replay import replay_rows
from kairospy.application.usecases.market.application.requests import MarketDataRow
from .query import MarketDataQueryService
from .resources import ExchangeName, MarketCommandResources, StorageFormat


class MarketReplayCommandService:
    def __init__(self, resources: MarketCommandResources, query: MarketDataQueryService | None = None) -> None:
        self.query = query or MarketDataQueryService(resources)

    def replay(
        self,
        *,
        dataset: str | None,
        root: str | None,
        storage_format: StorageFormat | None,
        symbol: str | None,
        exchange_name: ExchangeName,
        market: str,
        kind: str,
        timeframe: str | None,
        start: str | None,
        end: str | None,
        limit: int | None,
        speed: float,
        write: Callable[[MarketDataRow], None],
    ) -> None:
        rows = self.query.read(
            dataset=dataset,
            root=root,
            storage_format=storage_format,
            symbol=symbol,
            exchange_name=exchange_name,
            market=market,
            kind=kind,
            timeframe=timeframe,
            start=start,
            end=end,
            columns=None,
            limit=limit,
        )
        replay_rows(rows, speed=speed, write=write)


__all__ = ["MarketReplayCommandService"]
