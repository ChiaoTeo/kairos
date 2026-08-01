from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeVar

from kairospy.application.ports import MarketDataSubscriptionSpec, MarketStreamGateway
from kairospy.application.domain.account.bootstrap import AccountBootstrapGateway
from kairospy.core.account import AccountBookRef
from kairospy.infrastructure.integrations.credentials import credential_exists
from kairospy.infrastructure.integrations import DEFAULT_INTEGRATION_RESOLVER
from kairospy.infrastructure.integrations.market_stream import MarketStreamAdapter

ConfigErrorT = TypeVar("ConfigErrorT", bound=Exception)


@dataclass(frozen=True, slots=True)
class FeedConfig:
    feed_id: str
    credential: str | None = None
    values: Mapping[str, object] | None = None


def default_market_feed(venue: str, *, mode_label: str, error_type: type[ConfigErrorT]) -> MarketStreamGateway:
    return MarketStreamAdapter(DEFAULT_INTEGRATION_RESOLVER.market_feed(venue, mode_label=mode_label, error_type=error_type))


def default_market_feed_for_subscription(
    spec: MarketDataSubscriptionSpec,
    *,
    credential: str | None = None,
    mode_label: str,
    error_type: type[ConfigErrorT],
) -> MarketStreamGateway:
    return MarketStreamAdapter(
        DEFAULT_INTEGRATION_RESOLVER.market_feed_for_subscription(
            spec,
            credential=credential,
            mode_label=mode_label,
            error_type=error_type,
        )
    )


def default_broker(venue: str, credential: str | None, *, mode_label: str, error_type: type[ConfigErrorT]) -> AccountBootstrapGateway:
    return DEFAULT_INTEGRATION_RESOLVER.broker(venue, credential, mode_label=mode_label, error_type=error_type)


def default_broker_for_book(book: AccountBookRef, credential: str | None, *, mode_label: str, error_type: type[ConfigErrorT]) -> AccountBootstrapGateway:
    return DEFAULT_INTEGRATION_RESOLVER.broker_for_book(book, credential, mode_label=mode_label, error_type=error_type)


def parse_feeds(value: object, *, error_type: type[ConfigErrorT]) -> Mapping[str, FeedConfig]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise error_type("[feeds] must be a table")
    feeds: dict[str, FeedConfig] = {}
    for raw_id, raw in value.items():
        feed_id = str(raw_id).strip()
        if not feed_id:
            raise error_type("feeds id cannot be empty")
        if raw is None:
            raw_values: Mapping[str, object] = {}
        elif isinstance(raw, Mapping):
            raw_values = raw
        else:
            raise error_type(f"feeds.{feed_id} must be a table")
        credential = _optional_text(raw_values.get("credential"), f"feeds.{feed_id}.credential", error_type)
        feeds[_feed_key(feed_id)] = FeedConfig(feed_id=feed_id, credential=credential, values=dict(raw_values))
    return feeds


def configured_market_feed_for_subscription(
    spec: MarketDataSubscriptionSpec,
    *,
    feeds: Mapping[str, FeedConfig],
    mode_label: str,
    error_type: type[ConfigErrorT],
) -> MarketStreamGateway:
    feed = _feed_for_venue(feeds, str(spec.market.venue))
    if feed is None:
        venue = str(spec.market.venue)
        market = str(spec.market.market)
        symbol = str(spec.market.source_symbol)
        raise error_type(f"no configured feed for {mode_label} subscription: venue={venue} market={market} symbol={symbol}")
    if feed.credential is not None and not credential_exists(feed.credential):
        raise error_type(f"feed {feed.feed_id!r} references unknown credential: {feed.credential}")
    return default_market_feed_for_subscription(
        spec,
        credential=feed.credential,
        mode_label=mode_label,
        error_type=error_type,
    )


def _feed_for_venue(feeds: Mapping[str, FeedConfig], venue: str) -> FeedConfig | None:
    key = _feed_key(venue)
    if key in feeds:
        return feeds[key]
    for alias in _feed_aliases(key):
        if alias in feeds:
            return feeds[alias]
    return None


def _feed_aliases(key: str) -> tuple[str, ...]:
    aliases = {
        "okx": ("okex",),
        "okex": ("okx",),
    }
    return aliases.get(key, ())


def _feed_key(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _optional_text(value: object, source: str, error_type: type[ConfigErrorT]) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        raise error_type(f"{source} must be a non-empty string")
    return text


__all__ = [
    "FeedConfig",
    "configured_market_feed_for_subscription",
    "default_broker",
    "default_broker_for_book",
    "default_market_feed",
    "default_market_feed_for_subscription",
    "parse_feeds",
]
