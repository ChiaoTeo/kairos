"""Resource ports used by market command application services.

The command use cases select capabilities through this small consumer-owned
port.  Composition supplies the concrete workspace and integration adapter.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Protocol


class StorageFormat(str, Enum):
    parquet = "parquet"
    jsonl = "jsonl"


class ExchangeName(str, Enum):
    binance = "binance"
    hyperliquid = "hyperliquid"
    okex = "okex"
    okx = "okx"


class DriverName(str, Enum):
    ccxt = "ccxt"
    massive = "massive"


class MarketCommandResources(Protocol):
    def list_datasets(self, root: str | Path | None, *, storage_format: StorageFormat) -> object: ...
    def inspect_dataset(self, dataset: str, root: str | Path | None, *, storage_format: StorageFormat, sample: int) -> object: ...
    def alias_dataset(self, dataset: str, alias: str, root: str | Path | None, *, storage_format: StorageFormat) -> object: ...
    def prune_dataset(self, dataset: str, start: str, end: str, root: str | Path | None, *, storage_format: StorageFormat) -> object: ...

    def read_dataset(
        self,
        dataset: str,
        root: str | Path | None,
        *,
        storage_format: StorageFormat,
        start: str | None,
        end: str | None,
        columns: list[str] | None,
        limit: int | None,
    ) -> list[dict[str, object]]:
        """Read a named persisted dataset through the selected store."""

    def data_store(self, root: str | Path | None, storage_format: StorageFormat | None) -> object:
        """Return the selected market-data store."""

    def public_market_access(
        self,
        exchange_name: ExchangeName,
        driver_name: DriverName,
        *,
        product: object = ..., 
    ) -> object:
        """Return the selected public market access capability."""


__all__ = [
    "DriverName",
    "ExchangeName",
    "MarketCommandResources",
    "StorageFormat",
]
