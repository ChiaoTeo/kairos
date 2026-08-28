"""Pure exports for the Market owner-native current-view boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .types import _native

if TYPE_CHECKING:
    from kairospy._native_market_contract import (
        MarketBarCurrent,
        MarketCurrentEvidence,
        MarketCurrentView,
        MarketFreshnessCurrent,
        MarketFundingRateCurrent,
        MarketGreeksCurrent,
        MarketIndexPriceCurrent,
        MarketMarkPriceCurrent,
        MarketObservationScope,
        MarketOpenInterestCurrent,
        MarketOrderBookCurrent,
        MarketOrderBookLevel,
        MarketQuoteCurrent,
        MarketRateCurrent,
        MarketTicker24hCurrent,
        MarketViewKey,
        MarketViewKind,
    )
else:
    _module = _native()
    MarketBarCurrent = _module.MarketBarCurrent
    MarketCurrentEvidence = _module.MarketCurrentEvidence
    MarketCurrentView = _module.MarketCurrentView
    MarketFreshnessCurrent = _module.MarketFreshnessCurrent
    MarketFundingRateCurrent = _module.MarketFundingRateCurrent
    MarketGreeksCurrent = _module.MarketGreeksCurrent
    MarketIndexPriceCurrent = _module.MarketIndexPriceCurrent
    MarketMarkPriceCurrent = _module.MarketMarkPriceCurrent
    MarketObservationScope = _module.MarketObservationScope
    MarketOpenInterestCurrent = _module.MarketOpenInterestCurrent
    MarketOrderBookCurrent = _module.MarketOrderBookCurrent
    MarketOrderBookLevel = _module.MarketOrderBookLevel
    MarketQuoteCurrent = _module.MarketQuoteCurrent
    MarketRateCurrent = _module.MarketRateCurrent
    MarketTicker24hCurrent = _module.MarketTicker24hCurrent
    MarketViewKey = _module.MarketViewKey
    MarketViewKind = _module.MarketViewKind


__all__ = [name for name in globals() if name.startswith("Market")]
