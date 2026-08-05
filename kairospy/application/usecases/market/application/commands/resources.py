"""Resource ports used by market command application services.

The command use cases select capabilities through this small consumer-owned
port.  Composition supplies the concrete workspace and integration adapter.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from collections.abc import AsyncIterable
from typing import Protocol
from collections.abc import Mapping

from kairospy.application.usecases.market.application.requests import MarketDataRow, MarketOptions, MarketDatasetAliasResult, MarketDatasetInspectResult, MarketDatasetListResult, MarketDatasetPruneResult
from kairospy.application.usecases.market.protocol import MarketDataStore, MarketHistoricalClient


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
    ibkr = "ibkr"


class MarketStreamClient(Protocol):
    """Raw stream adapter; rows are normalized by the market command boundary."""

    def watch_ticker(self, symbol: str, *, params: MarketOptions | None = None) -> AsyncIterable[MarketDataRow]: ...
    def watch_order_book(self, symbol: str, *, limit: int | None = None, params: MarketOptions | None = None) -> AsyncIterable[MarketDataRow]: ...
    def watch_trades(self, symbol: str, *, limit: int = 1000, params: MarketOptions | None = None) -> AsyncIterable[MarketDataRow]: ...
    def watch_option_greeks(self, symbol: str, *, params: MarketOptions | None = None) -> AsyncIterable[MarketDataRow]: ...


class MarketCommandResources(Protocol):
    def list_datasets(self, root: str | Path | None, *, storage_format: StorageFormat) -> MarketDatasetListResult: ...
    def inspect_dataset(self, dataset: str, root: str | Path | None, *, storage_format: StorageFormat, sample: int) -> MarketDatasetInspectResult: ...
    def alias_dataset(self, dataset: str, alias: str, root: str | Path | None, *, storage_format: StorageFormat) -> MarketDatasetAliasResult: ...
    def prune_dataset(self, dataset: str, start: str, end: str, root: str | Path | None, *, storage_format: StorageFormat) -> MarketDatasetPruneResult: ...

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
    ) -> list[MarketDataRow]:
        """Read a named persisted dataset through the selected store."""

    def data_store(self, root: str | Path | None, storage_format: StorageFormat | None) -> MarketDataStore:
        """Return the selected market-data store."""

    def historical_market_access(
        self,
        exchange_name: ExchangeName,
        driver_name: DriverName,
    ) -> MarketHistoricalClient:
        ...

    def stream_market_access(
        self,
        exchange_name: ExchangeName,
        driver_name: DriverName,
    ) -> MarketStreamClient:
        ...


__all__ = [
    "DriverName",
    "ExchangeName",
    "MarketCommandResources",
    "MarketStreamClient",
    "StorageFormat",
]
