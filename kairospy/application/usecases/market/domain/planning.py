from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from types import MappingProxyType
from typing import Mapping

from kairospy.domain.market import Bar, MarketSelector, OptionGreeks, OrderBookSnapshot, Quote, RateObservation, TradePrint, market_selector
from kairospy.domain.reference import MarketRef

from ..domain.subscriptions import DataSubscription, MarketDataSubscriptionSpec
from .specs import MarketOptions


STREAM_TICKER = "ticker"
STREAM_ORDERBOOK = "orderbook"
STREAM_BAR = "bar"
STREAM_TRADE = "trade"
STREAM_MARKET_CONTEXT = "market_context"
STREAM_OPTION_GREEKS = "option_greeks"
STREAM_RATE = "rate"


class OpenInterest:
    pass


@dataclass(frozen=True, slots=True)
class MarketStreamPlan:
    key: str
    provider: str
    channel: str
    subject_type: str
    subject_id: str
    selectors: tuple[MarketSelector, ...]
    identity: str | None = None
    params: MarketOptions = MappingProxyType({})

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.channel.strip():
            raise ValueError("market stream plan key and channel are required")
        object.__setattr__(self, "selectors", tuple(self.selectors))
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))


@dataclass(frozen=True, slots=True)
class MarketFeedWatchPlan:
    key: str
    kind: str
    channel: str
    market: MarketRef
    source_symbol: str
    selector: MarketSelector
    params: MarketOptions = MappingProxyType({})
    depth: int | None = None

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.kind.strip() or not self.channel.strip():
            raise ValueError("market feed watch plan key, kind, and channel are required")
        if not self.source_symbol.strip():
            raise ValueError("market feed watch plan source_symbol is required")
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))


class MarketStreamPlanningService:
    def stream_plans(self, spec: MarketDataSubscriptionSpec) -> tuple[MarketStreamPlan, ...]:
        return plan_market_streams(spec)

    def feed_watches(self, subscription: DataSubscription) -> tuple[MarketFeedWatchPlan, ...]:
        plans: list[MarketFeedWatchPlan] = []
        spec = subscription.spec
        for selector in spec.selectors:
            plans.append(feed_watch_plan(subscription, selector))
        return tuple(plans)


def selector_channel(selector: MarketSelector | type) -> str:
    selector = market_selector(selector)
    model = selector.model
    if model is Quote:
        return STREAM_TICKER if selector.basis in {None, "ticker"} else str(selector.basis)
    if model is OrderBookSnapshot:
        return STREAM_ORDERBOOK
    if model is Bar:
        return STREAM_BAR
    if model is TradePrint:
        return STREAM_TRADE
    if model is RateObservation:
        return STREAM_MARKET_CONTEXT if selector.basis == "funding_rate" else STREAM_RATE
    if model is OptionGreeks:
        return STREAM_OPTION_GREEKS
    if model is OpenInterest:
        return STREAM_MARKET_CONTEXT
    return model.__name__.lower()


def plan_market_streams(spec: MarketDataSubscriptionSpec) -> tuple[MarketStreamPlan, ...]:
    selectors = tuple(market_selector(selector) for selector in spec.selectors)
    grouped: dict[str, list[MarketSelector]] = {}
    for selector in selectors:
        grouped.setdefault(selector_channel(selector), []).append(selector)
    plans: list[MarketStreamPlan] = []
    for channel, channel_selectors in sorted(grouped.items()):
        params = dict(spec.params)
        key = _stream_plan_key(spec, channel, tuple(channel_selectors), params)
        plans.append(
            MarketStreamPlan(
                key,
                str(spec.provider or spec.market.exchange_id),
                channel,
                "market",
                str(spec.market.market_id),
                tuple(channel_selectors),
                identity=spec.identity,
                params=params,
            )
        )
    return tuple(plans)


def feed_watch_plan(subscription: DataSubscription, selector: MarketSelector | type) -> MarketFeedWatchPlan:
    selector = market_selector(selector)
    channel = selector_channel(selector)
    model = selector.model
    params = dict(subscription.spec.params)
    if subscription.spec.provider is not None:
        params["provider"] = str(subscription.spec.provider)
    depth = None
    if model is Quote:
        kind = "quote"
    elif model is OrderBookSnapshot:
        kind = "orderbook"
        depth = getattr(selector, "depth", None)
        derivation = getattr(selector, "derivation", "direct")
        if derivation != "direct":
            params["derivation"] = derivation
        if depth == "full":
            params["orderbook_depth"] = "full"
            depth = None
    elif model is TradePrint:
        kind = "trade"
    elif model is OptionGreeks:
        kind = "option_greeks"
    else:
        raise ValueError(f"unsupported streaming market selector model: {getattr(model, '__name__', model)!r}")
    return MarketFeedWatchPlan(
        key=f"{subscription.key}.{channel}",
        kind=kind,
        channel=channel,
        market=subscription.spec.market,
        source_symbol=str(subscription.spec.market.source_symbol),
        selector=selector,
        params=params,
        depth=depth,
    )


def _stream_plan_key(spec: MarketDataSubscriptionSpec, channel: str, selectors: tuple[MarketSelector, ...], params: MarketOptions) -> str:
    subject = _key_part(spec.market.source_symbol or spec.market.market_id)
    identity_value = spec.identity
    identity = "" if identity_value is None else f".{_key_part(identity_value)}"
    options = "|".join([selector.key for selector in selectors] + [f"{name}={value}" for name, value in sorted(params.items())])
    digest = sha1(options.encode("utf-8")).hexdigest()[:12]
    provider = spec.provider or spec.market.exchange_id
    return ".".join(part for part in ("market", str(provider), channel, subject + identity, digest) if part)


def _key_part(value: object) -> str:
    return "".join(character if character.isalnum() else "_" for character in str(value).lower()).strip("_")


__all__ = [
    "STREAM_BAR",
    "STREAM_MARKET_CONTEXT",
    "STREAM_ORDERBOOK",
    "STREAM_OPTION_GREEKS",
    "STREAM_RATE",
    "STREAM_TICKER",
    "STREAM_TRADE",
    "MarketFeedWatchPlan",
    "MarketStreamPlan",
    "MarketStreamPlanningService",
    "OpenInterest",
    "feed_watch_plan",
    "plan_market_streams",
    "selector_channel",
]
