from __future__ import annotations

from kairospy.application.usecases.market.application.component import MarketApplication
from kairospy.application.usecases.market.application.data import MarketDataSpec
from .resources import ExchangeName, MarketCommandResources, StorageFormat


class MarketDataQueryService:
    """System query for a market dataset or a resolved market specification."""

    def __init__(self, resources: MarketCommandResources) -> None:
        self._resources = resources

    def read(
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
        columns: list[str] | None,
        limit: int | None,
    ) -> list[dict[str, object]]:
        if dataset is not None:
            return self._resources.read_dataset(
                dataset,
                root,
                storage_format=storage_format or StorageFormat.parquet,
                start=start,
                end=end,
                columns=columns,
                limit=limit,
            )
        if symbol is None:
            raise ValueError("dataset or --symbol is required")
        spec = MarketDataSpec(
            symbol=symbol,
            kind=kind,
            venue=exchange_name.value,
            market=market,
            timeframe=timeframe,
            start=start,
            end=end,
            limit=limit,
        )
        return MarketApplication(store=self._resources.data_store(root, storage_format)).queries.read(spec, columns=columns)


__all__ = ["MarketDataQueryService"]
