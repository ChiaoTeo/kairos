from __future__ import annotations

from enum import Enum
from pathlib import Path

from kairospy.application.support.composition.application.integrations import connect_binance_equity, connect_binance_spot_account, connect_binance_spot_execution, connect_binance_spot_public, integration_application
from kairospy.infrastructure.integrations.application.connections import IntegrationConnection, RuntimeMode
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.infrastructure.integrations.domain import AccessScope, ExchangeId, ExchangeRef, IntegrationRoute, ProductFamily, TransportKind
from kairospy.application.usecases.workspace.application.context import workspace as resolve_workspace
from kairospy.application.usecases.account.application.ports import AccountCommandResources
from kairospy.application.usecases.execution.application.ports import OrderCommandResources
from kairospy.application.usecases.reference.application.ports import ReferenceCommandResources
from kairospy.domain.account import AccountBookRef
from kairospy.infrastructure.persistence.application.market_data import DataStore
from kairospy.infrastructure.persistence.application.market_data import MarketDatasetApplicationService
from kairospy.infrastructure.persistence.application.reference import SqliteReferenceStore
from kairospy.application.usecases.market.application.commands.resources import DriverName, ExchangeName, StorageFormat
from kairospy.application.usecases.market.application.commands.resources import MarketCommandResources


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


def public_market_access(
    exchange_name: ExchangeName,
    driver_name: DriverName,
    *,
    product: ProductFamily = ProductFamily.SPOT,
) -> object:
    if driver_name is not DriverName.ccxt:
        raise ValueError("only ccxt driver is supported")
    if product is ProductFamily.EQUITY and exchange_name is ExchangeName.binance:
        return connect_binance_equity(
            f"facade.market.request.{exchange_name.value}.{driver_name.value}.{product.value}",
            mode=RuntimeMode.PAPER,
        )
    if product is ProductFamily.EQUITY and exchange_name is not ExchangeName.okx:
        raise ValueError(f"equity market is not supported for exchange: {exchange_name.value}")
    try:
        exchange = ExchangeId("okx" if exchange_name is ExchangeName.okex else exchange_name.value)
    except ValueError as error:
        raise ValueError(f"unsupported CCXT exchange: {exchange_name.value}") from error
    return integration_application().connect(
        IntegrationConnectionSpec(
            connection_id=f"facade.market.request.{exchange_name.value}.{driver_name.value}.{product.value}",
            route=IntegrationRoute(exchange=ExchangeRef(exchange)),
            product=product,
            access=AccessScope.PUBLIC,
            transport=TransportKind.REST,
            mode=RuntimeMode.PAPER,
        )
    )


class CompositionMarketCommandResources:
    """Composition adapter for market command resource selection."""

    @staticmethod
    def list_datasets(root: str | Path | None, *, storage_format: StorageFormat) -> object:
        return MarketDatasetApplicationService().list(root, storage_format=storage_format.value)

    @staticmethod
    def inspect_dataset(dataset: str, root: str | Path | None, *, storage_format: StorageFormat, sample: int) -> object:
        return MarketDatasetApplicationService().inspect(dataset, root, storage_format=storage_format.value, sample=sample)

    @staticmethod
    def alias_dataset(dataset: str, alias: str, root: str | Path | None, *, storage_format: StorageFormat) -> object:
        return MarketDatasetApplicationService().alias(dataset, alias, root, storage_format=storage_format.value)

    @staticmethod
    def prune_dataset(dataset: str, start: str, end: str, root: str | Path | None, *, storage_format: StorageFormat) -> object:
        return MarketDatasetApplicationService().prune(dataset, start, end, root, storage_format=storage_format.value)

    @staticmethod
    def read_dataset(
        dataset: str,
        root: str | Path | None,
        *,
        storage_format: StorageFormat,
        start: str | None,
        end: str | None,
        columns: list[str] | None,
        limit: int | None,
    ) -> list[dict[str, object]]:
        return MarketDatasetApplicationService().read(
            dataset,
            root,
            storage_format=storage_format.value,
            start=start,
            end=end,
            columns=columns,
            limit=limit,
        )

    data_store = staticmethod(data_store)
    public_market_access = staticmethod(public_market_access)
    reference_store = staticmethod(reference_store)

    @staticmethod
    def provider(provider_name: object, driver_name: DriverName) -> object:
        return provider(provider_name, driver_name)

    @staticmethod
    def reference_access(
        source_kind: str,
        source_name: str,
        *,
        market: str | None,
        driver_name: DriverName,
    ) -> IntegrationConnection:
        return reference_access(source_kind, source_name, market=market, driver_name=driver_name)


def market_command_resources() -> MarketCommandResources:
    return CompositionMarketCommandResources()


def command_resources() -> AccountCommandResources | OrderCommandResources | ReferenceCommandResources:
    return CompositionMarketCommandResources()


def private_account_access(
    book: AccountBookRef,
    driver_name: DriverName,
    *,
    credential: str | None = None,
) -> object:
    _require_ccxt_driver(driver_name)
    return connect_binance_spot_account(
        f"facade.account.request.{book.value}.{credential or 'default'}",
        credential=credential,
        mode=RuntimeMode.PAPER if credential is None else RuntimeMode.LIVE,
    )


def account_read_access(
    book: AccountBookRef,
    driver_name: DriverName,
    *,
    credential: str | None = None,
) -> object:
    _require_ccxt_driver(driver_name)
    return private_account_access(book, driver_name, credential=credential)


def account_query_access(
    book: AccountBookRef,
    driver_name: DriverName,
    *,
    credential: str | None = None,
) -> object:
    _require_ccxt_driver(driver_name)
    return private_account_access(book, driver_name, credential=credential)


def execution_access_for_account(
    book: AccountBookRef,
    driver_name: DriverName,
    *,
    credential: str | None = None,
) -> object:
    _require_ccxt_driver(driver_name)
    return execution_access(book, driver_name, credential=credential)


def execution_access(
    book: AccountBookRef,
    driver_name: DriverName,
    *,
    credential: str | None = None,
) -> IntegrationConnection:
    _require_ccxt_driver(driver_name)
    return connect_binance_spot_execution(
        f"facade.execution.{book.value}.{credential or 'default'}",
        credential=credential,
        mode=RuntimeMode.LIVE,
    )


def provider(
    provider_name: object,
    driver_name: DriverName,
) -> object:
    provider_value = getattr(provider_name, "value", provider_name)
    if str(provider_value) == ProviderName.massive.value:
        if driver_name is not DriverName.massive:
            raise ValueError("massive provider requires massive driver")
        raise ValueError("Massive provider integration is not part of Binance Spot convergence")
    raise ValueError(f"unsupported provider: {provider_value}")


def reference_access(
    source_kind: str,
    source_name: str,
    *,
    market: str | None,
    driver_name: DriverName,
) -> IntegrationConnection:
    if source_kind in {"exchange", "broker"} and driver_name is not DriverName.ccxt:
        raise ValueError(f"{source_kind} reference source requires ccxt driver")
    if source_kind == "provider" and driver_name is not DriverName.massive:
        raise ValueError("massive provider requires massive driver")
    if source_kind != "exchange" or source_name.lower() != ExchangeName.binance.value:
        raise ValueError("only Binance Spot reference catalog is supported")
    return connect_binance_spot_public(
        f"facade.reference.{source_kind}.{source_name}.{market or 'default'}",
        mode=RuntimeMode.PAPER,
    )


def _require_ccxt_driver(driver_name: DriverName) -> None:
    if driver_name is not DriverName.ccxt:
        raise ValueError("only ccxt driver is supported")


__all__ = [
    "DriverName",
    "ExchangeName",
    "ProviderName",
    "StorageFormat",
    "account_read_access",
    "account_query_access",
    "data_store",
    "command_resources",
    "market_command_resources",
    "execution_access",
    "execution_access_for_account",
    "private_account_access",
    "public_market_access",
    "provider",
    "reference_access",
    "reference_store",
]
