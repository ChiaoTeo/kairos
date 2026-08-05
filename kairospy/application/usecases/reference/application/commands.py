from __future__ import annotations

"""System-facing reference administration adapter."""

from enum import Enum

from kairospy.application.usecases.reference.application.ports import ReferenceCommandResources
from kairospy.application.usecases.reference.application.serde import asset_to_primitive, entity_to_primitive, instrument_to_primitive, lifecycle_event_to_primitive, listing_to_primitive, market_to_primitive
from kairospy.application.usecases.reference.protocol import ReferenceCatalogSource, ReferenceProviderSource
from kairospy.application.usecases.reference.application.requests import ReferenceDriverName as DriverName, ReferenceExchangeName as ExchangeName
from kairospy.application.usecases.workspace.application.context import workspace as resolve_workspace
from kairospy.domain.reference import AssetType, Broker, Exchange, MarketStatus, Provider
from kairospy.domain.reference import brokers as reference_brokers
from kairospy.domain.reference import exchanges as reference_exchanges
from kairospy.domain.reference import providers as reference_providers


class ProviderName(str, Enum):
    massive = "massive"


def workspace_cli_format() -> str | None:
    try:
        cli = resolve_workspace().manifest.values.get("cli")
    except Exception:
        return None
    if not isinstance(cli, dict):
        return None
    value = cli.get("format")
    return value if isinstance(value, str) else None


class ReferenceCommandApplication:
    def __init__(self, resources: ReferenceCommandResources) -> None:
        self._resources = resources

    def public_market_access(self, exchange_name: ExchangeName, driver_name: DriverName) -> ReferenceCatalogSource:
        return self._resources.public_market_access(exchange_name, driver_name)

    def provider(self, provider_name: ProviderName, driver_name: DriverName) -> ReferenceProviderSource:
        return self._resources.provider(provider_name, driver_name)

    def reference_access(self, source_kind: str, source_name: str, *, market: str | None, driver_name: DriverName) -> ReferenceCatalogSource:
        return self._resources.reference_access(source_kind, source_name, market=market, driver_name=driver_name)


__all__ = [
    "AssetType",
    "Broker",
    "DriverName",
    "Exchange",
    "ExchangeName",
    "MarketStatus",
    "Provider",
    "ProviderName",
    "ReferenceCommandApplication",
    "asset_to_primitive",
    "entity_to_primitive",
    "instrument_to_primitive",
    "lifecycle_event_to_primitive",
    "listing_to_primitive",
    "market_to_primitive",
    "reference_brokers",
    "reference_exchanges",
    "reference_providers",
    "workspace_cli_format",
]
