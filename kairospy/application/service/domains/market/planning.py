from __future__ import annotations

from hashlib import sha1
from types import MappingProxyType
from typing import Mapping

from kairospy.core.market import Bar, MarketSelector, OrderBookSnapshot, Quote, RateObservation, TradePrint


STREAM_TICKER = "ticker"
STREAM_ORDERBOOK = "orderbook"
STREAM_BAR = "bar"
STREAM_TRADE = "trade"
STREAM_MARKET_CONTEXT = "market_context"
STREAM_RATE = "rate"


class OpenInterest:
    pass


def selector_channel(selector: MarketSelector) -> str:
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
        if selector.basis == "funding_rate":
            return STREAM_MARKET_CONTEXT
        return STREAM_RATE
    if model is OpenInterest:
        return STREAM_MARKET_CONTEXT
    return model.__name__.lower()


class MarketStreamPlan:
    __slots__ = ("key", "provider", "channel", "subject_type", "subject_id", "selectors", "identity", "params")

    def __init__(
        self,
        key: str,
        provider: str,
        channel: str,
        subject_type: str,
        subject_id: str,
        selectors: tuple[MarketSelector, ...],
        *,
        identity: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> None:
        if not key.strip() or not channel.strip():
            raise ValueError("market stream plan key and channel are required")
        self.key = key
        self.provider = provider
        self.channel = channel
        self.subject_type = subject_type
        self.subject_id = subject_id
        self.selectors = tuple(selectors)
        self.identity = identity
        self.params = MappingProxyType(dict(params or {}))


def plan_market_streams(spec: object) -> tuple[MarketStreamPlan, ...]:
    selectors = tuple(getattr(spec, "selectors"))
    grouped: dict[str, list[MarketSelector]] = {}
    for selector in selectors:
        grouped.setdefault(selector_channel(selector), []).append(selector)
    plans: list[MarketStreamPlan] = []
    for channel, channel_selectors in sorted(grouped.items()):
        params = dict(getattr(spec, "params"))
        key = _stream_plan_key(spec, channel, tuple(channel_selectors), params)
        plans.append(
            MarketStreamPlan(
                key,
                getattr(spec, "venue") or "",
                channel,
                getattr(spec, "subject_type"),
                getattr(spec, "subject_id"),
                tuple(channel_selectors),
                identity=getattr(spec, "identity"),
                params=params,
            )
        )
    return tuple(plans)


def _stream_plan_key(
    spec: object,
    channel: str,
    selectors: tuple[MarketSelector, ...],
    params: Mapping[str, object],
) -> str:
    subject = _key_part(getattr(spec, "source_symbol") or getattr(spec, "subject_id"))
    identity_value = getattr(spec, "identity")
    identity = "" if identity_value is None else f".{_key_part(identity_value)}"
    options = "|".join(
        [selector.key for selector in selectors]
        + [f"{name}={value}" for name, value in sorted(params.items())]
    )
    digest = sha1(options.encode("utf-8")).hexdigest()[:12]
    return ".".join(part for part in ("market", getattr(spec, "venue") or "", channel, subject + identity, digest) if part)


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
