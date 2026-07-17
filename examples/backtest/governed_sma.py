"""Run one SMA strategy through batch and canonical async replay paths.

Default execution is a deterministic fixture and needs no data download. Pass
--dataset to consume an approved Q3/Q4 release from ResearchDataClient.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json

from trading.contracts import canonicalize_market_event
from trading.data import OutputFormat, ResearchDataClient, RunMode
from trading.domain.identity import InstrumentId
from trading.domain.market_data import Bar
from trading.market_data import IterableEventSource, MarketEventEnvelope, MarketEventType
from trading.storage.codec import to_primitive
from trading.strategies.sma_cross import (
    BarSeries, SmaCrossConfig, backtest_sma_cross, backtest_sma_cross_events,
)


def fixture_bars() -> tuple[Bar, ...]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    instrument = InstrumentId("crypto:binance:spot:BTCUSDT")
    values = []
    for index in range(90):
        close = Decimal("100") + Decimal(index % 30) - Decimal((index // 30) * 8)
        values.append(Bar(
            instrument, start + timedelta(hours=index), start + timedelta(hours=index + 1),
            close - Decimal("0.5"), close + Decimal("1"), close - Decimal("1"), close,
            Decimal("10") + index,
        ))
    return tuple(values)


def governed_bars(
    lake_root: str, dataset: str, start: str | None, end: str | None,
) -> tuple[str, tuple[Bar, ...]]:
    query = ResearchDataClient(lake_root, run_mode=RunMode.BACKTEST).get(
        dataset, start=start, end=end,
        fields=("instrument_id", "period_start", "period_end", "open", "high", "low", "close", "volume"),
    )
    rows = query.collect(OutputFormat.ROWS)
    bars = tuple(Bar(
        InstrumentId(str(row["instrument_id"])), _time(row["period_start"]), _time(row["period_end"]),
        Decimal(str(row["open"])), Decimal(str(row["high"])), Decimal(str(row["low"])),
        Decimal(str(row["close"])), Decimal(str(row["volume"])),
    ) for row in rows)
    return query.release_id, bars


def canonical_events(bars: tuple[Bar, ...]):
    for sequence, bar in enumerate(bars):
        source_event = MarketEventEnvelope(
            bar.instrument_id, bar.start, bar.end, bar.end, "example", "bars", bar.instrument_id.value,
            MarketEventType.BAR, sequence,
            {"period_start": bar.start, "period_end": bar.end, "open": bar.open, "high": bar.high,
             "low": bar.low, "close": bar.close, "volume": bar.volume},
            receive_time=bar.end,
        )
        yield canonicalize_market_event(source_event, source_instance="example-replay")


async def run(args) -> dict[str, object]:
    dataset_id, bars = (governed_bars(args.lake_root, args.dataset, args.start, args.end)
                        if args.dataset else ("fixture:sma-bars-v1", fixture_bars()))
    config = SmaCrossConfig(args.fast, args.slow, Decimal("100000"), Decimal(str(args.fee_bps)))
    batch = backtest_sma_cross(BarSeries(dataset_id, bars), config)
    replay = await backtest_sma_cross_events(
        IterableEventSource(tuple(canonical_events(bars))), dataset_id, config,
    )
    if batch != replay:
        raise RuntimeError("batch and canonical replay results diverged")
    material = json.dumps(to_primitive(batch), sort_keys=True, separators=(",", ":"))
    return {
        "dataset_id": dataset_id, "bars": len(bars), "trades": len(batch.trades),
        "final_equity": str(batch.metrics["final_equity"]),
        "batch_equals_canonical_replay": True,
        "audit_hash": sha256(material.encode()).hexdigest(),
    }


def _time(value) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake-root", default="data")
    parser.add_argument("--dataset")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--fast", type=int, default=5)
    parser.add_argument("--slow", type=int, default=15)
    parser.add_argument("--fee-bps", type=Decimal, default=Decimal("10"))
    print(json.dumps(asyncio.run(run(parser.parse_args())), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
