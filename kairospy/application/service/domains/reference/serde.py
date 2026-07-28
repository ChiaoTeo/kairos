from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping

from kairospy.core.reference.identity import AssetId, EntityId, InstrumentId, ListingId, MarketId
from kairospy.core.reference.model import (
    Asset,
    AssetType,
    Entity,
    EntityType,
    InstrumentDefinition,
    InstrumentType,
    LifecycleEvent,
    LifecycleEventType,
    ListingDefinition,
    MarketDefinition,
    MarketStatus,
)


def entity_to_primitive(item: Entity) -> dict[str, object]:
    return {
        "entity_id": str(item.entity_id),
        "entity_type": item.entity_type.value,
        "name": item.name,
        **_interval_fields(item),
        "metadata": _encode(item.metadata),
    }


def asset_to_primitive(item: Asset) -> dict[str, object]:
    return {
        "asset_id": str(item.asset_id),
        "asset_type": item.asset_type.value,
        "symbol": item.symbol,
        "name": item.name,
        "issuer_id": _optional_id(item.issuer_id),
        **_interval_fields(item),
        "metadata": _encode(item.metadata),
    }


def instrument_to_primitive(item: InstrumentDefinition) -> dict[str, object]:
    return {
        "instrument_id": str(item.instrument_id),
        "instrument_type": item.instrument_type.value,
        "base_asset_id": _optional_id(item.base_asset_id),
        "quote_asset_id": _optional_id(item.quote_asset_id),
        "display_name": item.display_name,
        "underlying_instrument_id": _optional_id(item.underlying_instrument_id),
        "expiry": _optional_time(item.expiry),
        "strike": _optional_decimal(item.strike),
        "option_right": item.option_right,
        "multiplier": _optional_decimal(item.multiplier),
        **_interval_fields(item),
        "metadata": _encode(item.metadata),
    }


def listing_to_primitive(item: ListingDefinition) -> dict[str, object]:
    return {
        "listing_id": str(item.listing_id),
        "instrument_id": str(item.instrument_id),
        "venue": item.venue,
        "trading_symbol": item.trading_symbol,
        "venue_instrument_id": item.venue_instrument_id,
        "currency_asset_id": _optional_id(item.currency_asset_id),
        "status": item.status.value,
        **_interval_fields(item),
        "metadata": _encode(item.metadata),
    }


def market_to_primitive(item: MarketDefinition) -> dict[str, object]:
    return {
        "market_id": str(item.market_id),
        "instrument_id": str(item.instrument_id),
        "listing_id": str(item.listing_id),
        "venue": item.venue,
        "market": item.market,
        "source_symbol": item.source_symbol,
        "status": item.status.value,
        "price_tick": _optional_decimal(item.price_tick),
        "amount_tick": _optional_decimal(item.amount_tick),
        "price_precision": item.price_precision,
        "amount_precision": item.amount_precision,
        "min_amount": _optional_decimal(item.min_amount),
        "min_notional": _optional_decimal(item.min_notional),
        "contract_size": _optional_decimal(item.contract_size),
        **_interval_fields(item),
        "metadata": _encode(item.metadata),
    }


def lifecycle_event_to_primitive(item: LifecycleEvent) -> dict[str, object]:
    return {
        "event_type": item.event_type.value,
        "event_time": item.event_time.isoformat(),
        "instrument_id": _optional_id(item.instrument_id),
        "listing_id": _optional_id(item.listing_id),
        "market_id": _optional_id(item.market_id),
        "venue": item.venue,
        "source_symbol": item.source_symbol,
        "previous": _encode(item.previous),
        "current": _encode(item.current),
    }


def entity_from_primitive(item: Mapping[str, object]) -> Entity:
    return Entity(
        EntityId(_required(item, "entity_id")),
        EntityType(_required(item, "entity_type")),
        _required(item, "name"),
        effective_from=_time(_required(item, "effective_from")),
        effective_to=_optional_datetime(item.get("effective_to")),
        metadata=_mapping(item.get("metadata")),
    )


def asset_from_primitive(item: Mapping[str, object]) -> Asset:
    return Asset(
        AssetId(_required(item, "asset_id")),
        AssetType(_required(item, "asset_type")),
        _required(item, "symbol"),
        name=_optional_text(item.get("name")),
        issuer_id=_optional_ref(item.get("issuer_id"), EntityId),
        effective_from=_time(_required(item, "effective_from")),
        effective_to=_optional_datetime(item.get("effective_to")),
        metadata=_mapping(item.get("metadata")),
    )


def instrument_from_primitive(item: Mapping[str, object]) -> InstrumentDefinition:
    return InstrumentDefinition(
        InstrumentId(_required(item, "instrument_id")),
        InstrumentType(_required(item, "instrument_type")),
        base_asset_id=_optional_ref(item.get("base_asset_id"), AssetId),
        quote_asset_id=_optional_ref(item.get("quote_asset_id"), AssetId),
        display_name=_optional_text(item.get("display_name")),
        underlying_instrument_id=_optional_ref(item.get("underlying_instrument_id"), InstrumentId),
        expiry=_optional_datetime(item.get("expiry")),
        strike=_optional_decimal_from_value(item.get("strike")),
        option_right=_optional_text(item.get("option_right")),
        multiplier=_optional_decimal_from_value(item.get("multiplier")),
        effective_from=_time(_required(item, "effective_from")),
        effective_to=_optional_datetime(item.get("effective_to")),
        metadata=_mapping(item.get("metadata")),
    )


def listing_from_primitive(item: Mapping[str, object]) -> ListingDefinition:
    return ListingDefinition(
        ListingId(_required(item, "listing_id")),
        InstrumentId(_required(item, "instrument_id")),
        _required(item, "venue"),
        _required(item, "trading_symbol"),
        venue_instrument_id=_optional_text(item.get("venue_instrument_id")),
        currency_asset_id=_optional_ref(item.get("currency_asset_id"), AssetId),
        status=MarketStatus(_required(item, "status")),
        effective_from=_time(_required(item, "effective_from")),
        effective_to=_optional_datetime(item.get("effective_to")),
        metadata=_mapping(item.get("metadata")),
    )


def market_from_primitive(item: Mapping[str, object]) -> MarketDefinition:
    return MarketDefinition(
        MarketId(_required(item, "market_id")),
        InstrumentId(_required(item, "instrument_id")),
        ListingId(_required(item, "listing_id")),
        _required(item, "venue"),
        _required(item, "market"),
        _required(item, "source_symbol"),
        status=MarketStatus(_required(item, "status")),
        price_tick=_optional_decimal_from_value(item.get("price_tick")),
        amount_tick=_optional_decimal_from_value(item.get("amount_tick")),
        price_precision=_optional_int(item.get("price_precision")),
        amount_precision=_optional_int(item.get("amount_precision")),
        min_amount=_optional_decimal_from_value(item.get("min_amount")),
        min_notional=_optional_decimal_from_value(item.get("min_notional")),
        contract_size=_optional_decimal_from_value(item.get("contract_size")),
        effective_from=_time(_required(item, "effective_from")),
        effective_to=_optional_datetime(item.get("effective_to")),
        metadata=_mapping(item.get("metadata")),
    )


def lifecycle_event_from_primitive(item: Mapping[str, object]) -> LifecycleEvent:
    return LifecycleEvent(
        LifecycleEventType(_required(item, "event_type")),
        _time(_required(item, "event_time")),
        instrument_id=_optional_ref(item.get("instrument_id"), InstrumentId),
        listing_id=_optional_ref(item.get("listing_id"), ListingId),
        market_id=_optional_ref(item.get("market_id"), MarketId),
        venue=_optional_text(item.get("venue")),
        source_symbol=_optional_text(item.get("source_symbol")),
        previous=_mapping(item.get("previous")),
        current=_mapping(item.get("current")),
    )


def _interval_fields(item: Any) -> dict[str, object]:
    return {
        "effective_from": item.effective_from.isoformat(),
        "effective_to": _optional_time(item.effective_to),
    }


def _encode(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    return value


def _optional_id(value: object | None) -> str | None:
    return None if value is None else str(value)


def _optional_time(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _optional_decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _required(item: Mapping[str, object], key: str) -> str:
    value = item.get(key)
    text = str(value).strip() if value is not None else ""
    if not text:
        raise ValueError(f"{key} is required")
    return text


def _time(value: object) -> datetime:
    return datetime.fromisoformat(str(value))


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if not text else _time(text)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_ref(value: object, factory: Any) -> Any | None:
    text = _optional_text(value)
    return None if text is None else factory(text)


def _optional_decimal_from_value(value: object) -> Decimal | None:
    text = _optional_text(value)
    return None if text is None else Decimal(text)


def _optional_int(value: object) -> int | None:
    text = _optional_text(value)
    return None if text is None else int(text)


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


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
