from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .store import ViewStore

if TYPE_CHECKING:
    from kairospy.core.account import AccountViewReader
    from kairospy.core.market import MarketViewReader
    from kairospy.core.reference import ReferenceViewReader


@dataclass(frozen=True, slots=True)
class DomainViewReader:
    source: ViewStore

    def get(self, key: str, default: object = None) -> object:
        return self.source.get(key, default)

    def require(self, key: str) -> object:
        return self.source.require(key)

    @property
    def market(self) -> MarketViewReader:
        from kairospy.core.market import MarketViewReader

        return MarketViewReader(self.source)

    @property
    def accounts(self) -> AccountViewReader:
        from kairospy.core.account import AccountViewReader

        return AccountViewReader(self.source)

    @property
    def reference(self) -> ReferenceViewReader:
        from kairospy.core.reference import ReferenceViewReader

        return ReferenceViewReader(self.source)


__all__ = ["DomainViewReader"]
