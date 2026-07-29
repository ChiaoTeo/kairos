from __future__ import annotations

from collections.abc import AsyncIterable, Callable
from typing import Mapping

from kairospy.application.service.domain.market import MarketDataOperationsService, MarketDataResolver, MarketDataSpec, replay_rows
from kairospy.application.system.facade.resources import DriverName, ExchangeName, StorageFormat, data_store, exchange
from kairospy.application.system.workspace import KairosWorkspace


class MarketDataFacade:
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
        client = exchange(exchange_name, driver_name)
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
        path = self._service(root, storage_format, exchange_name=exchange_name, market=market).download(spec, client, mode=mode)
        return str(path)

    def list_datasets(self, *, root: str | None, storage_format: StorageFormat | None) -> dict[str, object]:
        store = data_store(root, storage_format)
        datasets = [str(item) for item in store.list()]
        return {"root": str(store.root), "datasets": datasets, "aliases": store.aliases(), "count": len(datasets)}

    def inspect_dataset(
        self,
        *,
        dataset: str,
        root: str | None,
        storage_format: StorageFormat | None,
        sample: int,
    ) -> dict[str, object]:
        store = data_store(root, storage_format)
        rows = store.read_rows(dataset)
        times = [str(row.get("time")) for row in rows if row.get("time") is not None]
        data_path = store._existing_data_path(dataset)
        return {
            "dataset": str(store.resolve(dataset)),
            "path": str(data_path) if data_path is not None else None,
            "rows": len(rows),
            "start": min(times) if times else None,
            "end": max(times) if times else None,
            "columns": sorted({key for row in rows for key in row}),
            "sample": rows[:sample],
        }

    def alias_dataset(
        self,
        *,
        dataset: str,
        alias: str,
        root: str | None,
        storage_format: StorageFormat | None,
    ) -> dict[str, object]:
        store = data_store(root, storage_format)
        path = store.alias(dataset, alias)
        workspace = KairosWorkspace.resolve()
        workspace.operations.append("market.alias", target={"dataset": str(store.resolve(dataset)), "alias": alias}, payload={"path": path})
        return {"dataset": str(store.resolve(dataset)), "alias": alias, "path": str(path)}

    def prune(
        self,
        *,
        dataset: str,
        start: str,
        end: str,
        root: str | None,
        storage_format: StorageFormat | None,
    ) -> dict[str, object]:
        store = data_store(root, storage_format)
        result = store.delete_window(dataset, start=start, end=end)
        KairosWorkspace.resolve().operations.append("market.prune", target={"dataset": str(result["dataset"])}, payload=result)
        return result

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
            return data_store(root, storage_format).read_rows(
                dataset,
                start=start,
                end=end,
                columns=columns,
                limit=limit,
            )
        if symbol is None:
            raise ValueError("dataset or --symbol is required")
        return self._service(root, storage_format, exchange_name=exchange_name, market=market).read(
            MarketDataSpec(
                symbol=symbol,
                kind=kind,
                venue=exchange_name.value,
                market=market,
                timeframe=timeframe,
                start=start,
                end=end,
                limit=limit,
            ),
            columns=columns,
        )

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
        write: Callable[[list[dict[str, object]]], object],
    ) -> None:
        rows = self.read(
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

    def stream_events(
        self,
        *,
        exchange_name: ExchangeName,
        driver_name: DriverName,
        kind: str,
        symbol: str,
        limit: int | None,
        book_limit: int | None,
        trade_limit: int,
        poll_seconds: float,
    ) -> AsyncIterable[Mapping[str, object]]:
        client = exchange(exchange_name, driver_name)
        params: dict[str, object] = {"poll_seconds": poll_seconds}
        if limit is not None:
            params["max_events"] = limit
        if kind == "ticker":
            return client.watch_ticker(symbol, params=params)
        if kind == "orderbook":
            return client.watch_order_book(symbol, limit=book_limit, params=params)
        if kind == "trades":
            return client.watch_trades(symbol, limit=trade_limit, params=params)
        raise ValueError(f"unsupported market data stream kind: {kind}")

    async def persist(
        self,
        *,
        dataset: str | None,
        kind: str,
        symbol: str,
        root: str | None,
        storage_format: StorageFormat | None,
        exchange_name: ExchangeName,
        driver_name: DriverName,
        market: str,
        limit: int | None,
        book_limit: int | None,
        trade_limit: int,
        poll_seconds: float,
    ) -> int:
        spec = MarketDataSpec(symbol=symbol, kind=kind, venue=exchange_name.value, market=market, dataset=dataset)
        events = self.stream_events(
            exchange_name=exchange_name,
            driver_name=driver_name,
            kind=kind,
            symbol=symbol,
            limit=limit,
            book_limit=book_limit,
            trade_limit=trade_limit,
            poll_seconds=poll_seconds,
        )
        return await self._service(root, storage_format, exchange_name=exchange_name, market=market).persist(spec, events, limit=limit)

    def doctor(self, *, exchange_name: ExchangeName, driver_name: DriverName) -> dict[str, object]:
        exchange(exchange_name, driver_name)
        return {"valid": True, "exchange": exchange_name.value, "driver": driver_name.value}

    def _service(
        self,
        root: str | None,
        storage_format: StorageFormat | None,
        *,
        exchange_name: ExchangeName,
        market: str,
    ) -> MarketDataOperationsService:
        return MarketDataOperationsService(
            data_store(root, storage_format),
            resolver=MarketDataResolver(default_venue=exchange_name.value, default_market=market),
        )


__all__ = ["MarketDataFacade"]
