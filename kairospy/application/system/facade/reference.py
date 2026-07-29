from __future__ import annotations

from kairospy.application.service.domain.reference import (
    add_asset,
    refresh_equity_provider,
    refresh_instrument_provider,
    refresh_instrument_provider_with_delist_schedule,
    sync_lifecycle_events,
)
from kairospy.application.service.domain.reference.serde import (
    asset_to_primitive,
    entity_to_primitive,
    instrument_to_primitive,
    lifecycle_event_to_primitive,
    listing_to_primitive,
    market_to_primitive,
)
from kairospy.application.system.facade.context import workspace as resolve_workspace
from kairospy.core.reference import AssetType, Broker, Exchange, MarketStatus, Provider
from kairospy.core.reference import brokers as reference_brokers
from kairospy.core.reference import exchanges as reference_exchanges
from kairospy.core.reference import providers as reference_providers


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
    "Exchange",
    "MarketStatus",
    "Provider",
    "add_asset",
    "asset_to_primitive",
    "entity_to_primitive",
    "instrument_to_primitive",
    "lifecycle_event_to_primitive",
    "listing_to_primitive",
    "market_to_primitive",
    "reference_brokers",
    "reference_exchanges",
    "reference_providers",
    "refresh_equity_provider",
    "refresh_instrument_provider",
    "refresh_instrument_provider_with_delist_schedule",
    "sync_lifecycle_events",
    "workspace_cli_format",
]
