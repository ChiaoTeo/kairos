"""Resources used by the interactive/system facades.

These are command-facing adapters.  They are deliberately separate from the
runtime composition root: a CLI query must not import the mode composition
graph merely to obtain one connection or store.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from kairospy.application.support.system.application.facade.context import workspace as resolve_workspace
from kairospy.domain.account import AccountBookRef
from kairospy.application.support.composition.application.integrations import connect_binance_spot
from kairospy.infrastructure.integrations.application.connections import IntegrationConnection, RuntimeMode
from kairospy.infrastructure.persistence.application.market_data import DataStore
from kairospy.infrastructure.persistence.application.reference import SqliteReferenceStore


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


def reference_store(root: str | Path | None) -> SqliteReferenceStore:
    workspace = resolve_workspace()
    resolved_root = workspace.manifest.resolve_path(root) if root is not None else workspace.reference_root
    return SqliteReferenceStore(resolved_root)


def _connect(
    connection_id: str,
    *,
    credential: str | None = None,
    market: bool = False,
    account: bool = False,
    execution: bool = False,
    mode: RuntimeMode = RuntimeMode.LIVE,
):
    return connect_binance_spot(
        connection_id,
        credential=credential,
        market=market,
        account=account,
        execution=execution,
        mode=mode,
    )


def public_market_access(exchange_name: ExchangeName, driver_name: DriverName) -> IntegrationConnection:
    if exchange_name is not ExchangeName.binance or driver_name is not DriverName.ccxt:
        raise ValueError("only Binance Spot ccxt integration is currently available")
    return _connect("facade.market.request.binance.ccxt", market=True, mode=RuntimeMode.PAPER)


def private_account_access(book: AccountBookRef, driver_name: DriverName, *, credential: str | None = None):
    _require_ccxt(driver_name)
    return _connect(
        f"facade.account.request.{book.value}.{credential or 'default'}",
        account=True,
        credential=credential,
        mode=RuntimeMode.PAPER if credential is None else RuntimeMode.LIVE,
    )


def account_bootstrap_access(book: AccountBookRef, driver_name: DriverName, *, credential: str | None = None):
    return private_account_access(book, driver_name, credential=credential)


def account_query_access(book: AccountBookRef, driver_name: DriverName, *, credential: str | None = None):
    return private_account_access(book, driver_name, credential=credential)


def execution_access(book: AccountBookRef, driver_name: DriverName, *, credential: str | None = None) -> IntegrationConnection:
    _require_ccxt(driver_name)
    return _connect(
        f"facade.execution.{book.value}.{credential or 'default'}",
        account=True,
        execution=True,
        credential=credential,
        mode=RuntimeMode.LIVE,
    )


def provider(provider_name: ProviderName, driver_name: DriverName) -> object:
    if provider_name is ProviderName.massive and driver_name is DriverName.massive:
        raise ValueError("Massive provider integration is not part of Binance Spot convergence")
    raise ValueError(f"unsupported provider: {provider_name.value}")


def reference_access(source_kind: str, source_name: str, *, market: str | None, driver_name: DriverName) -> IntegrationConnection:
    if source_kind in {"exchange", "broker"} and driver_name is not DriverName.ccxt:
        raise ValueError(f"{source_kind} reference source requires ccxt driver")
    if source_kind == "provider" and driver_name is not DriverName.massive:
        raise ValueError("massive provider requires massive driver")
    if source_kind != "exchange" or source_name.lower() != ExchangeName.binance.value:
        raise ValueError("only Binance Spot reference catalog is supported")
    return public_market_access(ExchangeName.binance, DriverName.ccxt)


def _require_ccxt(driver_name: DriverName) -> None:
    if driver_name is not DriverName.ccxt:
        raise ValueError("only ccxt driver is supported")


__all__ = [
    "DriverName", "ExchangeName", "ProviderName", "StorageFormat",
    "account_bootstrap_access", "account_query_access", "data_store",
    "execution_access", "private_account_access", "public_market_access",
    "provider", "reference_access", "reference_store",
]
