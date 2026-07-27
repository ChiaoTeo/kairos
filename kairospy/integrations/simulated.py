from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator, Iterable, Mapping


@dataclass(frozen=True, slots=True)
class SimulatedProvider:
    rows: tuple[Mapping[str, object], ...] = ()
    events: tuple[Mapping[str, object], ...] = ()
    delay_seconds: float = 0.0
    name: str = "simulated"

    def fetch_ohlcv(
        self,
        symbol: str,
        *,
        timeframe: str = "1m",
        since: object | None = None,
        until: object | None = None,
        limit: int = 1000,
        params: Mapping[str, object] | None = None,
    ) -> Iterable[Mapping[str, object]]:
        del timeframe, since, until, params
        yielded = 0
        for row in self.rows:
            if row.get("symbol") not in {None, symbol}:
                continue
            yield dict(row)
            yielded += 1
            if yielded >= limit:
                break

    async def watch_trades(
        self,
        symbol: str,
        *,
        since: object | None = None,
        limit: int = 50,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        del since, params
        yielded = 0
        for event in self.events:
            if event.get("symbol") not in {None, symbol}:
                continue
            if self.delay_seconds > 0:
                await asyncio.sleep(self.delay_seconds)
            yield dict(event)
            yielded += 1
            if yielded >= limit:
                break
