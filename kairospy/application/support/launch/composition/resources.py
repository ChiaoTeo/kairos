from __future__ import annotations

from enum import Enum
from pathlib import Path

from kairospy.application.usecases.reference.store import ReferenceStore
from kairospy.application.usecases.reference.source import ReferenceCatalogSource
from kairospy.application.usecases.account.bootstrap import AccountBootstrapGateway
from kairospy.application.support.system.facade.context import workspace as resolve_workspace
from kairospy.core.account import AccountBookRef
from kairospy.infrastructure.persistence.market_data.catalog import DataStore
from kairospy.infrastructure.integrations.application import (
    AccountIntegrationApplicationService,
    MarketIntegrationApplicationService,
    OrderIntegrationApplicationService,
    ReferenceIntegrationApplicationService,
)
from kairospy.infrastructure.persistence.reference.sqlite_store import SqliteReferenceStore


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


def data_store(root: str | Path | None, storage_format: StorageFormat | None) -> DataStore:
    workspace = resolve_workspace()
    resolved_root = workspace.manifest.resolve_path(root) if root is not None else workspace.data_root
    resolved_format = storage_format.value if storage_format is not None else workspace.manifest.storage_format
    return DataStore(resolved_root, storage_format=resolved_format)


def reference_store(root: str | Path | None) -> ReferenceStore:
    workspace = resolve_workspace()
    resolved_root = workspace.manifest.resolve_path(root) if root is not None else workspace.reference_root
    return SqliteReferenceStore(resolved_root)


def exchange(
    exchange_name: ExchangeName,
    driver_name: DriverName,
) -> MarketIntegrationApplicationService:
    if driver_name is not DriverName.ccxt:
        raise ValueError("only ccxt driver is supported")
    return MarketIntegrationApplicationService(venue=exchange_name.value, mode_label="facade", error_type=ValueError)


def account_balance_client(book: AccountBookRef, driver_name: DriverName, *, credential: str | None = None) -> AccountIntegrationApplicationService:
    _require_ccxt_driver(driver_name)
    return AccountIntegrationApplicationService(book, credential=credential, mode_label="facade", error_type=ValueError)


def account_bootstrap_client(book: AccountBookRef, driver_name: DriverName, *, credential: str | None = None) -> AccountBootstrapGateway:
    _require_ccxt_driver(driver_name)
    return AccountIntegrationApplicationService(book, credential=credential, mode_label="facade", error_type=ValueError)


def order_query_client(book: AccountBookRef, driver_name: DriverName, *, credential: str | None = None) -> OrderIntegrationApplicationService:
    _require_ccxt_driver(driver_name)
    return OrderIntegrationApplicationService(book, credential=credential, mode_label="facade", error_type=ValueError)


def order_execution_client(book: AccountBookRef, driver_name: DriverName, *, credential: str | None = None) -> OrderIntegrationApplicationService:
    _require_ccxt_driver(driver_name)
    return OrderIntegrationApplicationService(book, credential=credential, mode_label="facade", error_type=ValueError)


def provider(provider_name: ProviderName, driver_name: DriverName) -> ReferenceCatalogSource:
    if provider_name is ProviderName.massive:
        if driver_name is not DriverName.massive:
            raise ValueError("massive provider requires massive driver")
        return ReferenceIntegrationApplicationService.provider(provider_name.value, default_market="equity", error_type=ValueError)
    raise ValueError(f"unsupported provider: {provider_name.value}")


def reference_client(source_kind: str, source_name: str, *, market: str | None, driver_name: DriverName) -> ReferenceCatalogSource:
    if source_kind in {"exchange", "broker"} and driver_name is not DriverName.ccxt:
        raise ValueError(f"{source_kind} reference source requires ccxt driver")
    if source_kind == "provider" and driver_name is not DriverName.massive:
        raise ValueError("massive provider requires massive driver")
    return ReferenceIntegrationApplicationService.source_ref(source_kind, source_name, market=market, error_type=ValueError)


def _require_ccxt_driver(driver_name: DriverName) -> None:
    if driver_name is not DriverName.ccxt:
        raise ValueError("only ccxt driver is supported")


__all__ = [
    "DriverName",
    "ExchangeName",
    "ProviderName",
    "StorageFormat",
    "account_balance_client",
    "account_bootstrap_client",
    "data_store",
    "exchange",
    "order_execution_client",
    "order_query_client",
    "provider",
    "reference_client",
    "reference_store",
]
