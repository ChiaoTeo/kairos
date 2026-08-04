"""Public reference selection queries used by strategy runtimes."""

from __future__ import annotations

from kairospy.domain.market.selection import MarketSelection, MarketSelectionQuery

ReferenceQuery = MarketSelectionQuery
ReferenceSelection = MarketSelection


__all__ = ["ReferenceQuery", "ReferenceSelection"]
