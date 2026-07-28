from __future__ import annotations

from enum import Enum
from pathlib import Path

import typer

from kairospy.config import load_config
from kairospy.data import DataStore
from kairospy.integrations import (
    BinanceMarketDataConnector,
    CcxtDriver,
    HyperliquidMarketDataConnector,
    Massive,
    MassiveDriver,
    OkxMarketDataConnector,
)
from kairospy.service.domains.reference import ReferenceStore


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


class ProviderName(str, Enum):
    massive = "massive"


def store(root: str | Path | None, storage_format: StorageFormat | None) -> DataStore:
    config = load_config()
    resolved_root = config.resolve_path(root) if root is not None else config.data_root
    resolved_format = storage_format.value if storage_format is not None else config.storage_format
    return DataStore(resolved_root, storage_format=resolved_format)


def reference_store(root: str | Path | None) -> ReferenceStore:
    config = load_config()
    resolved_root = config.resolve_path(root) if root is not None else config.reference_root
    return ReferenceStore(resolved_root)


def exchange(
    exchange_name: ExchangeName,
    driver_name: DriverName,
) -> BinanceMarketDataConnector | HyperliquidMarketDataConnector | OkxMarketDataConnector:
    if driver_name is not DriverName.ccxt:
        raise typer.BadParameter("only ccxt driver is supported")
    if exchange_name is ExchangeName.binance:
        return BinanceMarketDataConnector(CcxtDriver())
    if exchange_name is ExchangeName.hyperliquid:
        return HyperliquidMarketDataConnector(CcxtDriver())
    if exchange_name in (ExchangeName.okx, ExchangeName.okex):
        return OkxMarketDataConnector()
    raise typer.BadParameter(f"unsupported exchange: {exchange_name.value}")


def provider(provider_name: ProviderName, driver_name: DriverName) -> Massive:
    if provider_name is ProviderName.massive:
        if driver_name is not DriverName.massive:
            raise typer.BadParameter("massive provider requires massive driver")
        return Massive(MassiveDriver())
    raise typer.BadParameter(f"unsupported provider: {provider_name.value}")
