"""Public reference serialization boundary used by system surfaces.

These converters intentionally remain separate from the business facade: CLI
and other surfaces need a stable primitive representation, while the domain
models remain free of surface formatting concerns.
"""

from kairospy.application.usecases.reference.domain.serde import (
    asset_from_primitive,
    asset_to_primitive,
    entity_from_primitive,
    entity_to_primitive,
    instrument_from_primitive,
    instrument_to_primitive,
    lifecycle_event_from_primitive,
    lifecycle_event_to_primitive,
    listing_from_primitive,
    listing_to_primitive,
    market_from_primitive,
    market_to_primitive,
)

__all__ = [
    "asset_from_primitive",
    "asset_to_primitive",
    "entity_from_primitive",
    "entity_to_primitive",
    "instrument_from_primitive",
    "instrument_to_primitive",
    "lifecycle_event_from_primitive",
    "lifecycle_event_to_primitive",
    "listing_from_primitive",
    "listing_to_primitive",
    "market_from_primitive",
    "market_to_primitive",
]
