"""Mode-specific launch composition.

This module is the composition boundary for selecting concrete backtest,
paper, and live resources. Launch application owns the lifecycle around it;
composition owns the concrete resource graph.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Mapping

from kairospy.application.support.launch.application.configuration import (
    ConfiguredBacktest,
    ConfiguredLive,
    ConfiguredPaper,
)
from kairospy.application.support.launch.domain.modes import RuntimeMode

from .backtest import BacktestComposition
from .common import ComposedLaunch
from .live import LiveComposition
from .paper import PaperComposition
from .integrations import configured_market_feed_for_subscription


AccountLeaseRunner = Callable[..., object]
AccountStatusWriter = Callable[[Path, object], None]
ArtifactWriter = Callable[[Path, object, Mapping[str, object]], None]


class ConfiguredLaunchComposer:
    """Compose one configured launch without owning its lifecycle."""

    def backtest(self, configured: ConfiguredBacktest) -> ComposedLaunch:
        return BacktestComposition().compose(configured)

    def paper(self, configured: ConfiguredPaper) -> ComposedLaunch:
        return PaperComposition().compose(configured)

    def live(self, configured: ConfiguredLive) -> ComposedLaunch:
        return LiveComposition().compose(configured)


def market_feed_resolver_builder(mode_label: str, *, error_type: type[Exception] = ValueError) -> object:
    """Build the mode-specific market feed resolver used during config parsing."""

    class ConfiguredMarketFeedResolver:
        def __init__(self, feeds: Mapping[str, object]) -> None:
            self.feeds = feeds

        def resolve_market_feed(self, spec: object) -> object:
            return configured_market_feed_for_subscription(
                spec,  # type: ignore[arg-type]
                feeds=self.feeds,
                mode_label=mode_label,
                error_type=error_type,
            )

    class ConfiguredMarketFeedResolverBuilder:
        def build_market_feed_resolver(self, feeds: Mapping[str, object]) -> ConfiguredMarketFeedResolver:
            return ConfiguredMarketFeedResolver(feeds)

    return ConfiguredMarketFeedResolverBuilder()

__all__ = ["ConfiguredLaunchComposer", "market_feed_resolver_builder"]
