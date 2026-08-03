from __future__ import annotations

from typing import Protocol

from kairospy.domain.account import AccountScopeReader, AccountViewReader
from kairospy.domain.market import MarketViewReader
from kairospy.domain.reference import ReferenceViewReader


class StrategyViewSource(Protocol):
    def get(self, key: str, default: object = None) -> object:
        ...

    def require(self, key: str) -> object:
        ...


class StrategyViews:
    def __init__(self, views: StrategyViewSource) -> None:
        self.source = views

    def get(self, key: str, default: object = None) -> object:
        return self.source.get(key, default)

    def require(self, key: str) -> object:
        return self.source.require(key)

    @property
    def market(self) -> MarketViewReader:
        return MarketViewReader(self.source)

    @property
    def accounts(self) -> AccountViewReader:
        return AccountViewReader(self.source)

    @property
    def reference(self) -> ReferenceViewReader:
        return ReferenceViewReader(self.source)


StrategyMarketViews = MarketViewReader
StrategyAccountViews = AccountViewReader
StrategyAccountScope = AccountScopeReader
StrategyReferenceViews = ReferenceViewReader


__all__ = [
    "StrategyAccountScope",
    "StrategyAccountViews",
    "StrategyMarketViews",
    "StrategyReferenceViews",
    "StrategyViewSource",
    "StrategyViews",
]
