from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from kairospy.core.market import Bar
from kairospy.infrastructure.persistence.market_data.records import event_time
from kairospy.core.reference import MarketRef

from kairospy.infrastructure.integrations.types import IntegrationParams, RawPayload, RawPayloadStream


@dataclass(frozen=True, slots=True)
class SimulatedProvider:
    rows: tuple[RawPayload, ...] = ()
    events: tuple[RawPayload, ...] = ()
    delay_seconds: float = 0.0
    name: str = "simulated"

    def fetch_bars(
        self,
        symbol: str,
        *,
        timeframe: str = "1m",
        since: object | None = None,
        until: object | None = None,
        limit: int = 1000,
        adapter_options: IntegrationParams | None = None,
    ) -> Iterable[Bar]:
        del since, until, adapter_options
        market = MarketRef.ephemeral(venue=self.name, market="spot", source_symbol=symbol)
        yielded = 0
        for row in self.rows:
            if row.get("symbol") not in {None, symbol}:
                continue
            yield Bar(
                instrument_id=str(row.get("instrument_id") or market.instrument_id),
                market_id=str(row.get("market_id") or market.market_id),
                market_key=str(row.get("market_key") or market.market_key),
                time=_bar_time(row),
                timeframe=str(row.get("timeframe") or timeframe),
                open=_optional_decimal(row.get("open")),
                high=_optional_decimal(row.get("high")),
                low=_optional_decimal(row.get("low")),
                close=_optional_decimal(row.get("close")),
                volume=_optional_decimal(row.get("volume")),
                source=str(row.get("venue") or self.name),
            )
            yielded += 1
            if yielded >= limit:
                break

    async def watch_trades(
        self,
        symbol: str,
        *,
        since: object | None = None,
        limit: int = 50,
        params: IntegrationParams | None = None,
    ) -> RawPayloadStream:
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


def _bar_time(row: RawPayload) -> object:
    value = row.get("timestamp")
    if value is not None:
        return event_time(value)
    value = row.get("time")
    if value is None:
        raise ValueError("simulated bar row requires time or timestamp")
    if hasattr(value, "tzinfo"):
        return value
    from datetime import datetime

    return datetime.fromisoformat(str(value))


def _optional_decimal(value: object | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))
