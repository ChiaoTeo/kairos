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


__all__ = ["DataSink", "DataStore", "MarketDataCatalog", "create_market_data_store"]
