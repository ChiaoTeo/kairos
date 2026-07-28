from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from types import MappingProxyType
from typing import Mapping

from kairospy.core.market import Bar, MarketSelector, OrderBookSnapshot, Quote, RateObservation, TradePrint, market_selector


STREAM_TICKER = "ticker"
STREAM_ORDERBOOK = "orderbook"
STREAM_BAR = "bar"
STREAM_TRADE = "trade"
STREAM_MARKET_CONTEXT = "market_context"
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
    params: Mapping[str, object] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.channel.strip():
            raise ValueError("market stream plan key and channel are required")
        object.__setattr__(self, "selectors", tuple(self.selectors))
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))


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
    if model is OpenInterest:
        return STREAM_MARKET_CONTEXT
    return model.__name__.lower()


def plan_market_streams(spec: object) -> tuple[MarketStreamPlan, ...]:
    selectors = tuple(market_selector(selector) for selector in getattr(spec, "selectors"))
    grouped: dict[str, list[MarketSelector]] = {}
    for selector in selectors:
        grouped.setdefault(selector_channel(selector), []).append(selector)
    plans: list[MarketStreamPlan] = []
    for channel, channel_selectors in sorted(grouped.items()):
        params = dict(getattr(spec, "params", {}))
        key = _stream_plan_key(spec, channel, tuple(channel_selectors), params)
        plans.append(
            MarketStreamPlan(
                key,
                str(getattr(spec, "venue", "") or ""),
                channel,
                str(getattr(spec, "subject_type")),
                str(getattr(spec, "subject_id")),
                tuple(channel_selectors),
                identity=getattr(spec, "identity", None),
                params=params,
            )
        )
    return tuple(plans)


def _stream_plan_key(spec: object, channel: str, selectors: tuple[MarketSelector, ...], params: Mapping[str, object]) -> str:
    subject = _key_part(getattr(spec, "source_symbol", None) or getattr(spec, "subject_id"))
    identity_value = getattr(spec, "identity", None)
    identity = "" if identity_value is None else f".{_key_part(identity_value)}"
    options = "|".join([selector.key for selector in selectors] + [f"{name}={value}" for name, value in sorted(params.items())])
    digest = sha1(options.encode("utf-8")).hexdigest()[:12]
    return ".".join(part for part in ("market", getattr(spec, "venue", None) or "", channel, subject + identity, digest) if part)


def _key_part(value: object) -> str:
    return "".join(character if character.isalnum() else "_" for character in str(value).lower()).strip("_")


__all__ = [
    "STREAM_BAR",
    "STREAM_MARKET_CONTEXT",
    "STREAM_ORDERBOOK",
    "STREAM_RATE",
    "STREAM_TICKER",
    "STREAM_TRADE",
    "MarketStreamPlan",
    "OpenInterest",
    "plan_market_streams",
    "selector_channel",
]
