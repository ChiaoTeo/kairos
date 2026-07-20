from __future__ import annotations

from dataclasses import MISSING, fields, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints
from uuid import UUID

from kairos.domain.event import (
    BrokerConnected,
    BrokerDisconnected,
    DataWarningRaised,
    EventEnvelope,
    GreeksUpdated,
    OptionChainDiscovered,
    QuoteUpdated,
    TradeUpdated,
    UnderlyingPriceUpdated,
)
from kairos.domain.identity import AccountKey, InstitutionId, InstrumentId
from kairos.domain.market_data import FundingRate, Greeks, IndexPrice, MarkPrice, OpenInterest, OptionChain, Quote, Trade, TradingStatus, VolatilitySurfacePoint
from kairos.study_platform.snapshot import DataQualityIssue, InstrumentSnapshot, OptionCaptureSnapshot
from kairos.study_platform.spec import MarketDataType, OptionChainCaptureSpec

PAYLOAD_TYPES = {
    cls.__name__: cls
    for cls in (
        UnderlyingPriceUpdated,
        QuoteUpdated,
        TradeUpdated,
        GreeksUpdated,
        OptionChainDiscovered,
        BrokerConnected,
        BrokerDisconnected,
        DataWarningRaised,
        IndexPrice,
        MarkPrice,
        FundingRate,
        OpenInterest,
        TradingStatus,
        VolatilitySurfacePoint,
    )
}


def to_primitive(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, time):
        return {"$time": value.isoformat()}
    if isinstance(value, UUID):
        return {"$uuid": str(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, AccountKey):
        return {
            "institution_id": to_primitive(value.institution_id),
            "account_id": value.account_id,
            "account_type": to_primitive(value.account_type),
        }
    if is_dataclass(value):
        return {field.name: to_primitive(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): to_primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_primitive(item) for item in value]
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _decode_scalar(value: Any, target: Any) -> Any:
    if target is Decimal:
        return Decimal(value.get("$decimal", value))
    if target is datetime:
        return datetime.fromisoformat(value.get("$datetime", value))
    if target is date:
        return date.fromisoformat(value.get("$date", value))
    if target is UUID:
        return UUID(value.get("$uuid", value))
    if isinstance(target, type) and issubclass(target, Enum):
        return target(value)
    if target is time:
        return time.fromisoformat(value.get("$time", value))
    return value


def from_primitive(value: Any, target: Any) -> Any:
    origin, args = get_origin(target), get_args(target)
    if origin in (Union, UnionType):
        if value is None and type(None) in args:
            return None
        candidates = [arg for arg in args if arg is not type(None)]
        if len(candidates) == 1:
            return from_primitive(value, candidates[0])
    if origin in (tuple, list, set, frozenset):
        if origin is tuple and len(args) > 1 and args[-1] is not Ellipsis:
            decoded = [from_primitive(item, item_type) for item, item_type in zip(value, args)]
        else:
            item_type = args[0] if args else Any
            decoded = [from_primitive(item, item_type) for item in value]
        if origin is tuple:
            return tuple(decoded)
        if origin is set:
            return set(decoded)
        if origin is frozenset:
            return frozenset(decoded)
        return decoded
    if target in (Decimal, datetime, date, time, UUID) or isinstance(target, type) and issubclass(target, Enum):
        return _decode_scalar(value, target)
    if isinstance(target, type) and is_dataclass(target):
        hints = get_type_hints(target)
        decoded = {}
        for field in fields(target):
            if field.name in value:
                decoded[field.name] = from_primitive(value[field.name], hints[field.name])
            elif field.default is MISSING and field.default_factory is MISSING:
                raise KeyError(field.name)
        return target(**decoded)
    return value


def snapshot_from_primitive(value: dict[str, Any]) -> OptionCaptureSnapshot:
    from kairos.reference.repository import instrument_from_primitive
    return OptionCaptureSnapshot(
        schema_version=value["schema_version"],
        run_id=from_primitive(value["run_id"], UUID),
        created_at=from_primitive(value["created_at"], datetime),
        spec=from_primitive(value["spec"], OptionChainCaptureSpec),
        underlying_id=from_primitive(value["underlying_id"], InstrumentId),
        underlying_price=from_primitive(value["underlying_price"], Decimal),
        underlying_price_time=from_primitive(value["underlying_price_time"], datetime),
        option_chain=from_primitive(value["option_chain"], OptionChain),
        definitions=tuple(instrument_from_primitive(item) for item in value["definitions"]),
        instruments=tuple(from_primitive(item, InstrumentSnapshot) for item in value["instruments"]),
        sources=tuple(value["sources"]),
        quality_issues=tuple(from_primitive(item, DataQualityIssue) for item in value["quality_issues"]),
        snapshot_span_seconds=value["snapshot_span_seconds"],
        code_version=value["code_version"],
    )


def snapshot_to_primitive(snapshot: OptionCaptureSnapshot) -> dict[str, Any]:
    from kairos.reference.repository import instrument_to_primitive
    value = to_primitive(snapshot)
    value["definitions"] = [instrument_to_primitive(item) for item in snapshot.definitions]
    return value


def restore_primitives(value: Any) -> Any:
    if isinstance(value, list):
        return [restore_primitives(item) for item in value]
    if isinstance(value, dict):
        if set(value) == {"$decimal"}:
            return Decimal(value["$decimal"])
        if set(value) == {"$datetime"}:
            return datetime.fromisoformat(value["$datetime"])
        if set(value) == {"$date"}:
            return date.fromisoformat(value["$date"])
        if set(value) == {"$time"}:
            return time.fromisoformat(value["$time"])
        if set(value) == {"$uuid"}:
            return UUID(value["$uuid"])
        return {key: restore_primitives(item) for key, item in value.items()}
    return value


def event_to_primitive(event: EventEnvelope[Any]) -> dict[str, Any]:
    data = to_primitive(event)
    data["payload_type"] = type(event.payload).__name__
    data["schema_version"] = 1
    return data


def event_from_primitive(value: dict[str, Any]) -> EventEnvelope[Any]:
    payload_type = PAYLOAD_TYPES[value["payload_type"]]
    return EventEnvelope(
        event_id=from_primitive(value["event_id"], UUID),
        event_time=from_primitive(value["event_time"], datetime),
        received_time=from_primitive(value["received_time"], datetime),
        payload=from_primitive(value["payload"], payload_type),
        source=value["source"],
        sequence=value.get("sequence"),
        correlation_id=from_primitive(value["correlation_id"], UUID) if value.get("correlation_id") else None,
        schema_version=value.get("schema_version", 1),
        raw_payload_reference=value.get("raw_payload_reference"),
    )
