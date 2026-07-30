from __future__ import annotations

from kairospy.core.account import AccountScopeReader, AccountViewReader
from kairospy.core.market import MarketViewReader
from kairospy.core.reference import ReferenceViewReader
from kairospy.core.views import DomainViewReader, ViewStore


class StrategyViews(DomainViewReader):
    def __init__(self, views: ViewStore) -> None:
        super().__init__(views)


StrategyMarketViews = MarketViewReader
StrategyAccountViews = AccountViewReader
StrategyAccountScope = AccountScopeReader
StrategyReferenceViews = ReferenceViewReader


__all__ = [
    "StrategyAccountScope",
    "StrategyAccountViews",
    "StrategyMarketViews",
    "StrategyReferenceViews",
    "StrategyViews",
]
