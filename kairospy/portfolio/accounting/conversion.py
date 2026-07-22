from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from kairospy.identity import AssetId


@dataclass(frozen=True, slots=True)
class ConversionRate:
    base: AssetId
    quote: AssetId
    rate: Decimal
    timestamp: datetime
    source: str

    def __post_init__(self) -> None:
        if self.rate <= 0 or self.timestamp.tzinfo is None:
            raise ValueError("conversion rate must be positive and timezone-aware")


@dataclass(frozen=True, slots=True)
class ConversionResult:
    amount: Decimal
    path: tuple[AssetId, ...]
    oldest_timestamp: datetime
    sources: tuple[str, ...]


class AssetConversionGraph:
    def __init__(self) -> None:
        self._rates: dict[tuple[AssetId, AssetId], ConversionRate] = {}

    def update(self, rate: ConversionRate) -> None:
        self._rates[(rate.base, rate.quote)] = rate
        self._rates[(rate.quote, rate.base)] = ConversionRate(rate.quote, rate.base, Decimal("1") / rate.rate, rate.timestamp, rate.source)

    def convert(self, amount: Decimal, source: AssetId, target: AssetId, at: datetime, max_age: timedelta) -> ConversionResult:
        if source == target:
            return ConversionResult(amount, (source,), at, ())
        queue = deque([(source, Decimal("1"), (source,), at, ())])
        visited = {source}
        while queue:
            asset, aggregate, path, oldest, sources = queue.popleft()
            for (base, quote), rate in self._rates.items():
                if base != asset or quote in visited or rate.timestamp > at or at - rate.timestamp > max_age:
                    continue
                next_aggregate = aggregate * rate.rate
                next_path = (*path, quote)
                next_oldest = min(oldest, rate.timestamp)
                next_sources = (*sources, rate.source)
                if quote == target:
                    return ConversionResult(amount * next_aggregate, next_path, next_oldest, next_sources)
                visited.add(quote)
                queue.append((quote, next_aggregate, next_path, next_oldest, next_sources))
        raise LookupError(f"no fresh conversion path: {source} -> {target}")
