from __future__ import annotations

from pathlib import Path
from typing import Literal

from kairospy.infrastructure.persistence.services.market_data.catalog import DataStore, MarketDataCatalog
from kairospy.infrastructure.persistence.services.market_data.ingest import DataSink


def create_market_data_store(
    root: str | Path = ".kairos/data",
    *,
    storage_format: Literal["parquet", "jsonl"] = "parquet",
) -> MarketDataCatalog:
    return MarketDataCatalog(root, storage_format=storage_format)


class MarketDatasetApplicationService:
    """Persistence-facing dataset commands and queries."""

    def list(self, root: str | Path | None = None, *, storage_format: str = "parquet") -> dict[str, object]:
        store = _store(root, storage_format)
        datasets = [str(item) for item in store.list()]
        return {"root": str(store.root), "datasets": datasets, "aliases": store.aliases(), "count": len(datasets)}

    def inspect(
        self,
        dataset: str,
        root: str | Path | None = None,
        *,
        storage_format: str = "parquet",
        sample: int = 3,
    ) -> dict[str, object]:
        store = _store(root, storage_format)
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

    def alias(
        self,
        dataset: str,
        alias: str,
        root: str | Path | None = None,
        *,
        storage_format: str = "parquet",
    ) -> dict[str, object]:
        store = _store(root, storage_format)
        path = store.alias(dataset, alias)
        return {"dataset": str(store.resolve(dataset)), "alias": alias, "path": str(path)}

    def prune(
        self,
        dataset: str,
        start: str,
        end: str,
        root: str | Path | None = None,
        *,
        storage_format: str = "parquet",
    ) -> dict[str, object]:
        return _store(root, storage_format).delete_window(dataset, start=start, end=end)

    def read(
        self,
        dataset: str,
        root: str | Path | None = None,
        *,
        storage_format: str = "parquet",
        start: str | None = None,
        end: str | None = None,
        columns: list[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, object]]:
        return _store(root, storage_format).read_rows(
            dataset,
            start=start,
            end=end,
            columns=columns,
            limit=limit,
        )


def _store(root: str | Path | None, storage_format: str) -> DataStore:
    return DataStore(root or ".kairos/data", storage_format=str(storage_format))


__all__ = [
    "DataSink",
    "DataStore",
    "MarketDataCatalog",
    "MarketDatasetApplicationService",
    "create_market_data_store",
]
