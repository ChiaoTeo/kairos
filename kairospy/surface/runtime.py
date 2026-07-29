from __future__ import annotations

from enum import Enum
from pathlib import Path

import typer

from kairospy.application.system.workspace import KairosWorkspace
from kairospy.infrastructure.data import DataStore
from kairospy.infrastructure.integrations import (
    BinanceBroker,
    BinanceMarketDataConnector,
    CcxtDriver,
    HyperliquidMarketDataConnector,
    Massive,
    MassiveDriver,
    OkxBroker,
    OkxMarketDataConnector,
)
from kairospy.application.service.domain.reference import ReferenceStore


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
    workspace = KairosWorkspace.resolve()
    resolved_root = workspace.manifest.resolve_path(root) if root is not None else workspace.data_root
    resolved_format = storage_format.value if storage_format is not None else workspace.manifest.storage_format
    return DataStore(resolved_root, storage_format=resolved_format)


def reference_store(root: str | Path | None) -> ReferenceStore:
    workspace = KairosWorkspace.resolve()
    resolved_root = workspace.manifest.resolve_path(root) if root is not None else workspace.reference_root
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


def broker(exchange_name: ExchangeName, driver_name: DriverName, *, credential: str | None = None) -> BinanceBroker | OkxBroker:
    if driver_name is not DriverName.ccxt:
        raise typer.BadParameter("only ccxt driver is supported")
    if exchange_name is ExchangeName.binance:
        return BinanceBroker(CcxtDriver())
    if exchange_name in (ExchangeName.okx, ExchangeName.okex):
        return OkxBroker.from_credential(credential)
    raise typer.BadParameter(f"unsupported broker exchange: {exchange_name.value}")


def provider(provider_name: ProviderName, driver_name: DriverName) -> Massive:
    if provider_name is ProviderName.massive:
        if driver_name is not DriverName.massive:
            raise typer.BadParameter("massive provider requires massive driver")
        return Massive(MassiveDriver())
    raise typer.BadParameter(f"unsupported provider: {provider_name.value}")
