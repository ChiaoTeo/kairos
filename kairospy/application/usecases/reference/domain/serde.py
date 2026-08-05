from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from collections.abc import Callable, Mapping
from typing import TypeVar

from kairospy.domain.reference.identity import AssetId, EntityId, FinancialProductId, InstrumentId, ListingId, MarketId, ProviderId
from kairospy.domain.reference.model import (
    Asset,
    AssetType,
    Entity,
    EntityType,
    EffectiveInterval,
    FinancialProductDefinition,
    FinancialProductStatus,
    FinancialProductType,
    InstrumentDefinition,
    InstrumentType,
    LifecycleEvent,
    LifecycleEventType,
    ListingDefinition,
    MarketDefinition,
    MarketStatus,
)


ReferenceIdT = TypeVar("ReferenceIdT")


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


def financial_product_to_primitive(item: FinancialProductDefinition) -> dict[str, object]:
    return {
        "product_id": str(item.product_id),
        "product_type": item.product_type.value,
        "name": item.name,
        "asset_id": str(item.asset_id),
        "provider_product_id": item.provider_product_id,
        "provider_id": _optional_id(item.provider_id),
        "issuer_id": _optional_id(item.issuer_id),
        "currency_asset_id": _optional_id(item.currency_asset_id),
        "min_amount": _optional_decimal(item.min_amount),
        "max_amount": _optional_decimal(item.max_amount),
        "apr": _optional_decimal(item.apr),
        "lock_period_days": item.lock_period_days,
        "maturity_at": _optional_time(item.maturity_at),
        "status": item.status.value,
        **_interval_fields(item),
        "metadata": _encode(item.metadata),
    }


def listing_to_primitive(item: ListingDefinition) -> dict[str, object]:
    return {
        "listing_id": str(item.listing_id),
        "instrument_id": str(item.instrument_id),
        "exchange_id": str(item.exchange_id),
        "venue": str(item.venue),
        "listing_symbol": str(item.listing_symbol),
        "trading_symbol": str(item.trading_symbol),
        "exchange_instrument_id": _optional_id(item.exchange_instrument_id),
        "venue_instrument_id": _optional_id(item.venue_instrument_id),
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
        "exchange_id": str(item.exchange_id),
        "venue": str(item.venue),
        "market_type": str(item.market_type),
        "market": str(item.market),
        "source_symbol": str(item.source_symbol),
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
        "exchange_id": _optional_id(item.exchange_id),
        "venue": _optional_id(item.venue),
        "source_symbol": _optional_id(item.source_symbol),
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


def financial_product_from_primitive(item: Mapping[str, object]) -> FinancialProductDefinition:
    return FinancialProductDefinition(
        FinancialProductId(_required(item, "product_id")),
        FinancialProductType(_required(item, "product_type")),
        _required(item, "name"),
        AssetId(_required(item, "asset_id")),
        _required(item, "provider_product_id"),
        provider_id=_optional_ref(item.get("provider_id"), ProviderId),
        issuer_id=_optional_ref(item.get("issuer_id"), EntityId),
        currency_asset_id=_optional_ref(item.get("currency_asset_id"), AssetId),
        min_amount=_optional_decimal_from_value(item.get("min_amount")),
        max_amount=_optional_decimal_from_value(item.get("max_amount")),
        apr=_optional_decimal_from_value(item.get("apr")),
        lock_period_days=_optional_int(item.get("lock_period_days")),
        maturity_at=_optional_datetime(item.get("maturity_at")),
        status=FinancialProductStatus(_required(item, "status")),
        effective_from=_time(_required(item, "effective_from")),
        effective_to=_optional_datetime(item.get("effective_to")),
        metadata=_mapping(item.get("metadata")),
    )


def listing_from_primitive(item: Mapping[str, object]) -> ListingDefinition:
    return ListingDefinition(
        ListingId(_required(item, "listing_id")),
        InstrumentId(_required(item, "instrument_id")),
        _required_any(item, "exchange_id", "venue"),
        _required_any(item, "listing_symbol", "trading_symbol"),
        venue_instrument_id=_optional_text(item.get("exchange_instrument_id") or item.get("venue_instrument_id")),
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
        _required_any(item, "exchange_id", "venue"),
        _required_any(item, "market_type", "market"),
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
        venue=_optional_text(item.get("exchange_id") or item.get("venue")),
        source_symbol=_optional_text(item.get("source_symbol")),
        previous=_mapping(item.get("previous")),
        current=_mapping(item.get("current")),
    )


def _interval_fields(item: EffectiveInterval) -> dict[str, object]:
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


def _required_any(item: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        text = str(value).strip() if value is not None else ""
        if text:
            return text
    raise ValueError(f"{' or '.join(keys)} is required")


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


def _optional_ref(value: object, factory: Callable[[str], ReferenceIdT]) -> ReferenceIdT | None:
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
    "financial_product_from_primitive",
    "financial_product_to_primitive",
    "lifecycle_event_from_primitive",
    "lifecycle_event_to_primitive",
    "listing_from_primitive",
    "listing_to_primitive",
    "market_from_primitive",
    "market_to_primitive",
]
