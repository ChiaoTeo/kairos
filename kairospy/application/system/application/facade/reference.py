from __future__ import annotations

from kairospy.application.usecases.reference.application.operations import (
    add_asset,
    refresh_exchange_reference,
    refresh_exchange_reference_with_delist_schedule,
    refresh_equity_provider,
    refresh_instrument_provider,
    refresh_instrument_provider_with_delist_schedule,
    refresh_provider_reference,
    sync_lifecycle_events,
)
from kairospy.application.usecases.reference.application.serde import asset_to_primitive, entity_to_primitive, instrument_to_primitive, lifecycle_event_to_primitive, listing_to_primitive, market_to_primitive
from kairospy.application.usecases.reference.application.builders import catalog_from_equity_rows
from kairospy.application.support.system.application.facade.resources import (
    DriverName,
    ExchangeName,
    ProviderName,
    public_market_access,
    provider,
    reference_access,
    reference_store,
)
from kairospy.application.support.system.application.facade.context import workspace as resolve_workspace
from kairospy.domain.reference import AssetType, Broker, Exchange, MarketStatus, Provider
from kairospy.domain.reference import brokers as reference_brokers
from kairospy.domain.reference import exchanges as reference_exchanges
from kairospy.domain.reference import providers as reference_providers


def workspace_cli_format() -> str | None:
    try:
        cli = resolve_workspace().manifest.values.get("cli")
    except Exception:
        return None
    if not isinstance(cli, dict):
        return None
    value = cli.get("format")
    return value if isinstance(value, str) else None


__all__ = [
    "AssetType",
    "Broker",
    "DriverName",
    "Exchange",
    "ExchangeName",
    "MarketStatus",
    "Provider",
    "ProviderName",
    "add_asset",
    "asset_to_primitive",
    "entity_to_primitive",
    "public_market_access",
    "instrument_to_primitive",
    "lifecycle_event_to_primitive",
    "listing_to_primitive",
    "market_to_primitive",
    "provider",
    "reference_brokers",
    "reference_access",
    "reference_exchanges",
    "reference_providers",
    "reference_store",
    "refresh_exchange_reference",
    "refresh_exchange_reference_with_delist_schedule",
    "refresh_equity_provider",
    "refresh_instrument_provider",
    "refresh_instrument_provider_with_delist_schedule",
    "refresh_provider_reference",
    "sync_lifecycle_events",
    "workspace_cli_format",
]
