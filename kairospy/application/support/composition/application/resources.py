from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from kairospy.application.support.composition.application.integrations import connect_binance_equity, connect_binance_options_execution, connect_binance_options_account, connect_binance_spot_account, connect_binance_spot_execution, connect_binance_spot_public, connect_ibkr, connect_massive_reference, integration_application
from kairospy.infrastructure.integrations.application.connections import IntegrationConnection, RuntimeMode
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.infrastructure.integrations.domain import AccessScope, BrokerId, BrokerRef, CredentialRef, ExchangeId, ExchangeRef, IntegrationCapability, IntegrationRoute, ProductFamily, TransportKind
from kairospy.application.usecases.workspace.application.context import workspace as resolve_workspace
from kairospy.application.usecases.account.application.ports import AccountCommandResources, AccountCredentialProfile
from kairospy.application.usecases.account.protocol import AccountReadPort, AccountReadRequest
from kairospy.infrastructure.integrations.application.account import ConnectionAccountReadRequest
from kairospy.application.usecases.execution.application.ports import OrderCommandResources
from kairospy.application.usecases.reference.application.ports import ReferenceCommandResources
from kairospy.application.usecases.reference.application.requests import ReferenceDriverName, ReferenceExchangeName
from kairospy.application.usecases.reference.protocol import ReferenceCatalogSource, ReferenceProviderSource
from kairospy.domain.account import AccountRuntimeContext, AccountSegment, Environment
from kairospy.infrastructure.persistence.application.market_data import DataStore
from kairospy.infrastructure.persistence.application.market_data import MarketDatasetApplicationService
from kairospy.infrastructure.persistence.application.reference import SqliteReferenceStore
from kairospy.application.usecases.market.application.commands.resources import DriverName, ExchangeName, StorageFormat
from kairospy.application.usecases.market.application.commands.resources import MarketCommandResources
from kairospy.application.usecases.market.application.commands.resources import MarketStreamClient
from kairospy.application.usecases.market.protocol import MarketDataStore, MarketHistoricalClient
from kairospy.application.usecases.market.application.requests import MarketDataRow, MarketDatasetAliasResult, MarketDatasetInspectResult, MarketDatasetListResult, MarketDatasetPruneResult


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


def historical_market_access(
    exchange_name: ExchangeName,
    driver_name: DriverName,
    *,
    product: ProductFamily = ProductFamily.SPOT,
) -> MarketHistoricalClient:
    return public_market_access(exchange_name, driver_name, product=product)


def stream_market_access(exchange_name: ExchangeName, driver_name: DriverName) -> MarketStreamClient:
    return public_market_access(exchange_name, driver_name)


class CompositionMarketCommandResources:
    """Composition adapter for market command resource selection."""

    @staticmethod
    def list_datasets(root: str | Path | None, *, storage_format: StorageFormat) -> MarketDatasetListResult:
        value = MarketDatasetApplicationService().list(root, storage_format=storage_format.value)
        return {
            "root": str(value["root"]),
            "datasets": tuple(str(item) for item in value["datasets"]),
            "aliases": {str(key): str(item) for key, item in dict(value["aliases"]).items()},
            "count": int(value["count"]),
        }

    @staticmethod
    def inspect_dataset(dataset: str, root: str | Path | None, *, storage_format: StorageFormat, sample: int) -> MarketDatasetInspectResult:
        value = MarketDatasetApplicationService().inspect(dataset, root, storage_format=storage_format.value, sample=sample)
        return {
            "dataset": str(value["dataset"]),
            "path": None if value["path"] is None else str(value["path"]),
            "rows": int(value["rows"]),
            "start": None if value["start"] is None else str(value["start"]),
            "end": None if value["end"] is None else str(value["end"]),
            "columns": tuple(str(item) for item in value["columns"]),
            "sample": tuple(dict(row) for row in value["sample"]),
        }

    @staticmethod
    def alias_dataset(dataset: str, alias: str, root: str | Path | None, *, storage_format: StorageFormat) -> MarketDatasetAliasResult:
        value = MarketDatasetApplicationService().alias(dataset, alias, root, storage_format=storage_format.value)
        return {"dataset": str(value["dataset"]), "alias": str(value["alias"]), "path": str(value["path"])}

    @staticmethod
    def prune_dataset(dataset: str, start: str, end: str, root: str | Path | None, *, storage_format: StorageFormat) -> MarketDatasetPruneResult:
        value = MarketDatasetApplicationService().prune(dataset, start, end, root, storage_format=storage_format.value)
        return {"dataset": str(value["dataset"]), "deleted_rows": int(value["deleted_rows"]), "remaining_rows": int(value["remaining_rows"])}

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
    ) -> list[MarketDataRow]:
        return [dict(row) for row in MarketDatasetApplicationService().read(
            dataset,
            root,
            storage_format=storage_format.value,
            start=start,
            end=end,
            columns=columns,
            limit=limit,
        )]

    data_store = staticmethod(data_store)
    historical_market_access = staticmethod(historical_market_access)
    stream_market_access = staticmethod(stream_market_access)
    reference_store = staticmethod(reference_store)

    @staticmethod
    def public_market_access(
        exchange_name: ExchangeName | ReferenceExchangeName,
        driver_name: DriverName | ReferenceDriverName,
        *,
        product: ProductFamily = ProductFamily.SPOT,
    ) -> object:
        return public_market_access(
            ExchangeName(getattr(exchange_name, "value", exchange_name)),
            DriverName(getattr(driver_name, "value", driver_name)),
            product=product,
        )

    @staticmethod
    def account_reader(segment: AccountSegment, driver_name: DriverName, *, credential: str | None = None) -> AccountReadPort:
        return account_reader(segment, driver_name, credential=credential)

    @staticmethod
    def credential_profile(segment: AccountSegment, driver_name: DriverName, *, credential: str | None = None) -> AccountCredentialProfile:
        return credential_profile(segment, driver_name, credential=credential)

    @staticmethod
    def private_account_access(segment: AccountSegment, driver_name: DriverName, *, credential: str | None = None) -> object:
        return private_account_access(segment, driver_name, credential=credential)

    @staticmethod
    def account_read_access(segment: AccountSegment, driver_name: DriverName, *, credential: str | None = None) -> object:
        return account_read_access(segment, driver_name, credential=credential)

    @staticmethod
    def account_query_access(segment: AccountSegment, driver_name: DriverName, *, credential: str | None = None) -> object:
        return account_query_access(segment, driver_name, credential=credential)

    @staticmethod
    def provider(provider_name: str, driver_name: DriverName | ReferenceDriverName) -> ReferenceProviderSource:
        return provider(provider_name, DriverName(getattr(driver_name, "value", driver_name)))

    @staticmethod
    def reference_access(
        source_kind: str,
        source_name: str,
        *,
        market: str | None,
        driver_name: DriverName | ReferenceDriverName,
    ) -> ReferenceCatalogSource:
        return reference_access(
            source_kind,
            source_name,
            market=market,
            driver_name=DriverName(getattr(driver_name, "value", driver_name)),
        )


def market_command_resources() -> MarketCommandResources:
    return CompositionMarketCommandResources()


def command_resources() -> AccountCommandResources | OrderCommandResources | ReferenceCommandResources:
    return CompositionMarketCommandResources()


def private_account_access(
    segment: AccountSegment,
    driver_name: DriverName,
    *,
    credential: str | None = None,
) -> object:
    if str(segment.broker).lower() == BrokerId.IBKR.value:
        return connect_ibkr(
            f"facade.account.request.{segment.value}.{credential or 'default'}",
            credential=credential,
            mode=RuntimeMode.PAPER if credential is None else RuntimeMode.LIVE,
            capability=IntegrationCapability.ACCOUNT_READ,
        )
    _require_ccxt_driver(driver_name)
    product_name = str(segment.product_family or segment.segment_id).lower()
    if product_name == ProductFamily.OPTIONS.value:
        return connect_binance_options_account(
            f"facade.account.request.{segment.value}.{credential or 'default'}",
            credential=credential,
            mode=RuntimeMode.PAPER if credential is None else RuntimeMode.LIVE,
        )
    if product_name in {ProductFamily.USD_M_FUTURES.value, ProductFamily.COIN_M_FUTURES.value}:
        product = ProductFamily(product_name)
        return integration_application().connect(
            IntegrationConnectionSpec(
                connection_id=f"facade.account.request.{segment.value}.{credential or 'default'}",
                route=IntegrationRoute(
                    exchange=ExchangeRef(ExchangeId.BINANCE),
                    broker=BrokerRef(BrokerId.BINANCE),
                ),
                product=product,
                access=AccessScope.PRIVATE,
                transport=TransportKind.REST,
                capability=IntegrationCapability.ACCOUNT_READ,
                credential=CredentialRef(credential) if credential else None,
                mode=RuntimeMode.PAPER if credential is None else RuntimeMode.LIVE,
            )
        )
    if product_name != ProductFamily.SPOT.value:
        raise ValueError(
            f"Binance account segment {product_name!r} has no dedicated private account reader; "
            "inspect the external account before querying this segment"
        )
    return connect_binance_spot_account(
        f"facade.account.request.{segment.value}.{credential or 'default'}",
        credential=credential,
        mode=RuntimeMode.PAPER if credential is None else RuntimeMode.LIVE,
    )


def account_read_access(
    segment: AccountSegment,
    driver_name: DriverName,
    *,
    credential: str | None = None,
) -> object:
    return private_account_access(segment, driver_name, credential=credential)


def account_query_access(
    segment: AccountSegment,
    driver_name: DriverName,
    *,
    credential: str | None = None,
) -> object:
    return private_account_access(segment, driver_name, credential=credential)


@dataclass(frozen=True, slots=True)
class _ConfiguredAccountReader:
    connection: object

    def read_account(self, request: AccountReadRequest):
        reader = getattr(self.connection, "read_account", None)
        if not callable(reader):
            raise RuntimeError("account connection does not expose read_account")
        data = reader(
            ConnectionAccountReadRequest(
                context=request.context,
                observed_at=request.observed_at,
                symbol=request.symbol,
                fetch_orders=request.fetch_orders,
            )
        )
        return getattr(data, "snapshot", data)


def account_reader(
    segment: AccountSegment,
    driver_name: DriverName,
    *,
    credential: str | None = None,
) -> AccountReadPort:
    return _ConfiguredAccountReader(private_account_access(segment, driver_name, credential=credential))


def credential_profile(
    segment: AccountSegment,
    driver_name: DriverName,
    *,
    credential: str | None = None,
) -> AccountCredentialProfile:
    """Convert connector-specific credential discovery into account facts."""
    connection = private_account_access(segment, driver_name, credential=credential)
    inspector = getattr(connection, "inspect_credential", None)
    if callable(inspector):
        value = inspector()
        if not isinstance(value, Mapping):
            raise ValueError(f"credential {credential or 'default'} inspection did not return an object")
        return _credential_profile_from_payload(value)

    fetch_balance = getattr(connection, "fetch_balance", None)
    if callable(fetch_balance):
        try:
            fetch_balance(params={})
        except Exception as error:
            raise ValueError(f"credential {credential or 'default'} cannot read private account data") from error
        return AccountCredentialProfile(permissions=frozenset({"read"}), segments=(str(segment.product_family or segment.segment_id),))

    reader = getattr(connection, "read_account", None)
    if callable(reader):
        try:
            reader(
                ConnectionAccountReadRequest(
                    context=AccountRuntimeContext(segment, Environment.LIVE if credential else Environment.PAPER),
                    observed_at=datetime.now(timezone.utc),
                    fetch_orders=False,
                )
            )
        except Exception as error:
            raise ValueError(f"credential {credential or 'default'} cannot read private account data") from error
        return AccountCredentialProfile(permissions=frozenset({"read"}), segments=(str(segment.product_family or segment.segment_id),))

    raise ValueError(f"credential {credential or 'default'} cannot read private account data")


def _credential_profile_from_payload(payload: Mapping[str, object]) -> AccountCredentialProfile:
    permissions = _profile_permissions(payload)
    segments: list[str] = []
    for key in ("segments", "products", "product_families", "scopes"):
        value = payload.get(key)
        if isinstance(value, str):
            segments.append(value)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            segments.extend(str(item) for item in value)
    attributes = {
        str(key): str(value)
        for key, value in payload.items()
        if key not in {"api_key", "secret", "api_secret", "private_key"}
        and isinstance(value, (str, int, float, bool))
    }
    return AccountCredentialProfile(
        remote_identity=_credential_identity(payload),
        account_type=_text(payload.get("account_type")),
        permissions=frozenset(permissions),
        segments=tuple(segments),
        attributes=attributes,
    )


def _profile_permissions(profile: Mapping[str, object]) -> set[str]:
    values: set[str] = set()
    for key in ("capabilities", "permissions", "segments"):
        raw = profile.get(key)
        if isinstance(raw, str):
            values.add(_permission_key(raw))
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            values.update(_permission_key(str(item)) for item in raw)
        elif isinstance(raw, Mapping):
            values.update(_permission_key(str(name)) for name, enabled in raw.items() if bool(enabled))
    for key, permission in (
        ("can_read_private", "read"),
        ("read_private", "read"),
        ("read", "read"),
        ("can_trade", "trade"),
        ("trade", "trade"),
        ("trade_orders", "trade"),
    ):
        if profile.get(key) is True:
            values.add(permission)
    return values


def _permission_key(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    return {
        "readonly": "read",
        "read_only": "read",
        "read_private": "read",
        "order": "trade",
        "orders": "trade",
        "trade_orders": "trade",
        "place_order": "trade",
        "place_orders": "trade",
    }.get(normalized, normalized)


def _credential_identity(profile: Mapping[str, object]) -> str | None:
    for key in ("account_key", "account_id", "uid", "user_id", "master_account_id", "sub_account", "subaccount"):
        value = profile.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
        if isinstance(value, int):
            return str(value)
    account = profile.get("account")
    if isinstance(account, Mapping):
        return _credential_identity(account)
    return None


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def execution_access_for_account(
    segment: AccountSegment,
    driver_name: DriverName,
    *,
    credential: str | None = None,
) -> object:
    return execution_access(segment, driver_name, credential=credential)


def execution_access(
    segment: AccountSegment,
    driver_name: DriverName,
    *,
    credential: str | None = None,
) -> IntegrationConnection:
    if str(segment.broker).lower() == BrokerId.IBKR.value:
        return connect_ibkr(
            f"facade.execution.{segment.value}.{credential or 'default'}",
            credential=credential,
            mode=RuntimeMode.PAPER if credential is None else RuntimeMode.LIVE,
            capability=IntegrationCapability.ORDER_ENTRY,
        )
    _require_ccxt_driver(driver_name)
    if str(segment.product_family or segment.segment_id).lower() == ProductFamily.OPTIONS.value:
        return connect_binance_options_execution(
            f"facade.execution.{segment.value}.{credential or 'default'}",
            credential=credential,
            mode=RuntimeMode.PAPER if credential is None else RuntimeMode.LIVE,
        )
    return connect_binance_spot_execution(
            f"facade.execution.{segment.value}.{credential or 'default'}",
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
    credential: str | None = None,
) -> IntegrationConnection:
    if source_kind in {"exchange", "broker"} and driver_name is not DriverName.ccxt:
        raise ValueError(f"{source_kind} reference source requires ccxt driver")
    if source_kind == "provider" and driver_name is not DriverName.massive:
        raise ValueError("massive provider requires massive driver")
    if source_kind == "provider" and source_name.lower() == ProviderName.massive.value:
        return connect_massive_reference(
            f"facade.reference.{source_kind}.{source_name}.{market or 'default'}",
            credential=credential,
            mode=RuntimeMode.PAPER,
        )
    if source_kind != "exchange" or source_name.lower() != ExchangeName.binance.value:
        raise ValueError("only Binance reference catalogs or Massive reference catalogs are supported")
    if (market or "").casefold() in {"option", "options"}:
        return connect_binance_options(
            f"facade.reference.{source_kind}.{source_name}.{market or 'options'}",
            transport=TransportKind.REST,
            mode=RuntimeMode.PAPER,
        )
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
    "account_reader",
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
