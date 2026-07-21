from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import json
from typing import Mapping

from kairospy.trading.capability import MarketDataCapabilities, MarketDataKind
from kairospy.trading.identity import InstrumentId, VenueId
from kairospy.reference import ProviderId, ReferenceCatalog
from kairospy.reference.access import definition_at, product_type
from kairospy.reference.contracts import MappingTargetType


class DeliveryMode(StrEnum):
    ORDERED = "ordered"
    LATEST = "latest"


class CapturePolicy(StrEnum):
    NONE = "none"
    CANONICAL = "canonical"
    RAW_AND_CANONICAL = "raw_and_canonical"


@dataclass(frozen=True, slots=True)
class MarketDataRequirement:
    consumer_id: str
    provider_id: ProviderId
    instruments: tuple[InstrumentId, ...]
    kinds: tuple[MarketDataKind, ...]
    delivery: DeliveryMode = DeliveryMode.ORDERED
    maximum_age_seconds: int = 30
    depth: int | None = None
    capture: CapturePolicy = CapturePolicy.CANONICAL
    source_namespace: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.provider_id, VenueId):
            object.__setattr__(self, "provider_id", ProviderId(self.provider_id.value))
        if not self.consumer_id.strip():
            raise ValueError("market data requirement consumer id cannot be empty")
        if not self.instruments or not self.kinds:
            raise ValueError("market data requirement needs instruments and kinds")
        if len(set(self.instruments)) != len(self.instruments) or len(set(self.kinds)) != len(self.kinds):
            raise ValueError("market data requirement values must be unique")
        if self.maximum_age_seconds <= 0:
            raise ValueError("market data maximum age must be positive")
        if self.depth is not None and self.depth <= 0:
            raise ValueError("market data depth must be positive")
        if self.depth is not None and MarketDataKind.ORDER_BOOK not in self.kinds:
            raise ValueError("market data depth is only valid for order-book requirements")

    @property
    def venue_id(self) -> VenueId:
        return VenueId(self.provider_id.value)


@dataclass(frozen=True, slots=True, order=True)
class SubscriptionKey:
    provider_id: ProviderId
    instrument_id: InstrumentId
    symbol: str
    kind: MarketDataKind
    depth: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.provider_id, VenueId):
            object.__setattr__(self, "provider_id", ProviderId(self.provider_id.value))

    @property
    def venue_id(self) -> VenueId:
        return VenueId(self.provider_id.value)


@dataclass(frozen=True, slots=True)
class PlannedSubscription:
    key: SubscriptionKey
    consumers: tuple[str, ...]
    delivery: DeliveryMode
    maximum_age_seconds: int
    capture: CapturePolicy


@dataclass(frozen=True, slots=True)
class SubscriptionPlan:
    revision: str
    generated_at: datetime
    subscriptions: tuple[PlannedSubscription, ...]

    def __post_init__(self) -> None:
        if self.generated_at.tzinfo is None:
            raise ValueError("subscription plan timestamp must be timezone-aware")
        if not self.revision.strip():
            raise ValueError("subscription plan revision cannot be empty")

    @property
    def keys(self) -> frozenset[SubscriptionKey]:
        return frozenset(item.key for item in self.subscriptions)


class SubscriptionAction(StrEnum):
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"


@dataclass(frozen=True, slots=True)
class SubscriptionCommand:
    action: SubscriptionAction
    key: SubscriptionKey
    plan_revision: str


class SubscriptionPlanner:
    def __init__(self, catalog: ReferenceCatalog,
                 capabilities: Mapping[VenueId | ProviderId, MarketDataCapabilities]) -> None:
        self.catalog = catalog
        self.capabilities = {key.value: value for key, value in capabilities.items()}

    def build(self, requirements: tuple[MarketDataRequirement, ...], at: datetime) -> SubscriptionPlan:
        if at.tzinfo is None:
            raise ValueError("subscription planning timestamp must be timezone-aware")
        grouped: dict[SubscriptionKey, list[MarketDataRequirement]] = {}
        for requirement in requirements:
            capabilities = self.capabilities.get(requirement.provider_id.value)
            if capabilities is None:
                raise LookupError(f"no market data connector registered for provider {requirement.provider_id}")
            for kind in requirement.kinds:
                capabilities.require_market_data(kind)
            for instrument_id in requirement.instruments:
                definition = definition_at(self.catalog, instrument_id, at)
                capabilities.require_product(product_type(definition))
                symbol = self._symbol(requirement, instrument_id, at)
                for kind in requirement.kinds:
                    depth = requirement.depth if kind is MarketDataKind.ORDER_BOOK else None
                    key = SubscriptionKey(requirement.provider_id, instrument_id, symbol, kind, depth)
                    grouped.setdefault(key, []).append(requirement)
        subscriptions = tuple(PlannedSubscription(
            key,
            tuple(sorted({item.consumer_id for item in values})),
            DeliveryMode.ORDERED if any(item.delivery is DeliveryMode.ORDERED for item in values) else DeliveryMode.LATEST,
            min(item.maximum_age_seconds for item in values),
            max((item.capture for item in values), key=_capture_rank),
        ) for key, values in sorted(grouped.items(), key=lambda item: _key_tuple(item[0])))
        material = [{
            "provider": item.key.provider_id.value,
            "instrument": item.key.instrument_id.value,
            "symbol": item.key.symbol,
            "kind": item.key.kind.value,
            "depth": item.key.depth,
            "consumers": item.consumers,
            "delivery": item.delivery.value,
            "maximum_age_seconds": item.maximum_age_seconds,
            "capture": item.capture.value,
        } for item in subscriptions]
        revision = sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
        return SubscriptionPlan(revision, at, subscriptions)

    def _symbol(self, requirement: MarketDataRequirement, instrument_id: InstrumentId, at: datetime) -> str:
        mappings = [
            item for item in self.catalog.mappings()
            if item.provider_id == requirement.provider_id and item.active_at(at)
            and item.target_type is MappingTargetType.INSTRUMENT and item.target_id == instrument_id.value
            and (requirement.source_namespace is None or item.namespace == requirement.source_namespace)
        ]
        if len(mappings) == 1:
            return mappings[0].external_id
        listings = [item for item in self.catalog.active_listings(instrument_id, at) if item.venue_id.value == requirement.provider_id.value]
        if len(listings) == 1:
            return listings[0].trading_symbol
        raise LookupError(f"market-data symbol not found or ambiguous: {requirement.provider_id}/{instrument_id} at {at}")


class SubscriptionReconciler:
    def __init__(self) -> None:
        self._actual: frozenset[SubscriptionKey] = frozenset()
        self.last_revision: str | None = None

    def commands(self, plan: SubscriptionPlan) -> tuple[SubscriptionCommand, ...]:
        remove = sorted(self._actual - plan.keys, key=_key_tuple)
        add = sorted(plan.keys - self._actual, key=_key_tuple)
        return tuple(
            [SubscriptionCommand(SubscriptionAction.UNSUBSCRIBE, key, plan.revision) for key in remove]
            + [SubscriptionCommand(SubscriptionAction.SUBSCRIBE, key, plan.revision) for key in add]
        )

    def commit(self, plan: SubscriptionPlan) -> None:
        self._actual = plan.keys
        self.last_revision = plan.revision

    def reset_after_disconnect(self) -> None:
        self._actual = frozenset()

    @property
    def actual(self) -> frozenset[SubscriptionKey]:
        return self._actual


def _capture_rank(value: CapturePolicy) -> int:
    return (CapturePolicy.NONE, CapturePolicy.CANONICAL, CapturePolicy.RAW_AND_CANONICAL).index(value)


def _key_tuple(value: SubscriptionKey) -> tuple[str, str, str, str, int]:
    return value.provider_id.value, value.instrument_id.value, value.symbol, value.kind.value, value.depth or 0
