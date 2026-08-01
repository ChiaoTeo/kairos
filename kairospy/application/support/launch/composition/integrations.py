from __future__ import annotations

from collections.abc import Mapping
from typing import TypeVar

from kairospy.application.usecases.account.bootstrap import AccountBootstrapGateway
from kairospy.application.support.launch.config.common.integrations import FeedConfig
from kairospy.application.usecases.market.subscriptions import MarketDataSubscriptionSpec
from kairospy.application.support.runtime.services.market.feed import MarketStreamGateway
from kairospy.core.account import AccountBookRef
from kairospy.infrastructure.integrations.adapters.market_stream import MarketStreamAdapter
from kairospy.infrastructure.integrations.services.credentials import credential_exists
from kairospy.infrastructure.integrations.services.resolver import DEFAULT_INTEGRATION_RESOLVER

ConfigErrorT = TypeVar("ConfigErrorT", bound=Exception)


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
        DEFAULT_INTEGRATION_RESOLVER.market_feed_for_market(
            str(spec.market.venue),
            str(spec.market.market),
            credential=credential,
            mode_label=mode_label,
            error_type=error_type,
        )
    )


def default_broker(venue: str, credential: str | None, *, mode_label: str, error_type: type[ConfigErrorT]) -> AccountBootstrapGateway:
    return DEFAULT_INTEGRATION_RESOLVER.broker(venue, credential, mode_label=mode_label, error_type=error_type)


def default_broker_for_book(book: AccountBookRef, credential: str | None, *, mode_label: str, error_type: type[ConfigErrorT]) -> AccountBootstrapGateway:
    return DEFAULT_INTEGRATION_RESOLVER.broker_for_book(book, credential, mode_label=mode_label, error_type=error_type)


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


__all__ = [
    "configured_market_feed_for_subscription",
    "default_broker",
    "default_broker_for_book",
    "default_market_feed",
    "default_market_feed_for_subscription",
]
