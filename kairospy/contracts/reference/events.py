"""Reference-owned classified event contract."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias, cast

from kairospy.infrastructure.protocol.eventing import EventMetadataRead

if TYPE_CHECKING:
    from kairospy._native_reference_contract import (
        ReferenceAsset,
        ReferenceEvent,
        ReferenceExchange,
        ReferenceInstrument,
        ReferenceListing,
        ReferenceMarket,
    )


class _ReferenceEventBase(Protocol):
    metadata: EventMetadataRead
    catalog_revision: int


class ReferenceExchangeUpsertedEvent(_ReferenceEventBase, Protocol):
    kind: Literal["exchange_upserted"]
    data: ReferenceExchange


class ReferenceExchangeUpdatedEvent(_ReferenceEventBase, Protocol):
    kind: Literal["exchange_updated"]
    data: ReferenceExchange


class ReferenceAssetUpsertedEvent(_ReferenceEventBase, Protocol):
    kind: Literal["asset_upserted"]
    data: ReferenceAsset


class ReferenceAssetUpdatedEvent(_ReferenceEventBase, Protocol):
    kind: Literal["asset_updated"]
    data: ReferenceAsset


class ReferenceInstrumentUpsertedEvent(_ReferenceEventBase, Protocol):
    kind: Literal["instrument_upserted"]
    data: ReferenceInstrument


class ReferenceInstrumentUpdatedEvent(_ReferenceEventBase, Protocol):
    kind: Literal["instrument_updated"]
    data: ReferenceInstrument


class ReferenceListingUpsertedEvent(_ReferenceEventBase, Protocol):
    kind: Literal["listing_upserted"]
    data: ReferenceListing


class ReferenceListingUpdatedEvent(_ReferenceEventBase, Protocol):
    kind: Literal["listing_updated"]
    data: ReferenceListing


class ReferenceMarketUpsertedEvent(_ReferenceEventBase, Protocol):
    kind: Literal["market_upserted"]
    data: ReferenceMarket


class ReferenceMarketUpdatedEvent(_ReferenceEventBase, Protocol):
    kind: Literal["market_updated"]
    data: ReferenceMarket


ReferenceEventVariant: TypeAlias = (
    ReferenceExchangeUpsertedEvent
    | ReferenceExchangeUpdatedEvent
    | ReferenceAssetUpsertedEvent
    | ReferenceAssetUpdatedEvent
    | ReferenceInstrumentUpsertedEvent
    | ReferenceInstrumentUpdatedEvent
    | ReferenceListingUpsertedEvent
    | ReferenceListingUpdatedEvent
    | ReferenceMarketUpsertedEvent
    | ReferenceMarketUpdatedEvent
)


def decode_event(frame: bytes) -> ReferenceEventVariant:
    return cast(
        ReferenceEventVariant,
        import_module("kairospy._native_reference_contract").decode_event(frame),
    )


if not TYPE_CHECKING:
    ReferenceEvent = import_module("kairospy._native_reference_contract").ReferenceEvent


__all__ = [
    "ReferenceAssetUpdatedEvent",
    "ReferenceAssetUpsertedEvent",
    "ReferenceEvent",
    "ReferenceEventVariant",
    "ReferenceExchangeUpdatedEvent",
    "ReferenceExchangeUpsertedEvent",
    "ReferenceInstrumentUpdatedEvent",
    "ReferenceInstrumentUpsertedEvent",
    "ReferenceListingUpdatedEvent",
    "ReferenceListingUpsertedEvent",
    "ReferenceMarketUpdatedEvent",
    "ReferenceMarketUpsertedEvent",
    "decode_event",
]
