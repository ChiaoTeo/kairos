from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from .ccxt import CcxtDriver


@dataclass(frozen=True, slots=True)
class BinanceReferenceDriver:
    driver: CcxtDriver = field(default_factory=CcxtDriver)

    def fetch_delist_schedule(self, *, params: Mapping[str, object] | None = None) -> Iterable[Mapping[str, object]]:
        return self.driver.fetch_binance_spot_delist_schedule(params=params)


__all__ = ["BinanceReferenceDriver"]
