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
from trading.application import GovernedStrategyRunLoop, run_target_backtest
from trading.data import OutputFormat, ResearchDataClient, RunMode
from trading.domain.identity import InstrumentId
from trading.domain.market_data import Bar
from trading.market_data import IterableEventSource, MarketEventEnvelope, MarketEventType
from trading.storage.codec import to_primitive
from trading.strategies.sma_cross import (
    BarSeries, SmaCrossConfig, backtest_sma_cross, backtest_sma_cross_events,
)
from trading.features import SmaFactorConfig, SmaFactorRuntime
from trading.strategies import GovernedStrategyRuntime, SmaCrossStrategy, SmaCrossStrategyConfig, StrategyContext
from trading.strategies.specs import sma_strategy_spec


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
    strategy_spec, policy = sma_strategy_spec(config)
    governed = await GovernedStrategyRunLoop(
        IterableEventSource(tuple(canonical_events(bars))),
        SmaFactorRuntime(
            SmaFactorConfig(args.fast, args.slow), input_identity=dataset_id,
        ),
        GovernedStrategyRuntime(
            SmaCrossStrategy(SmaCrossStrategyConfig(bars[0].instrument_id)), strategy_spec,
            execution_policy_id=policy.policy_id,
        ),
        lambda market: StrategyContext(market, object(), (), object()),
        approved_capital=config.initial_cash,
    ).run()
    immediate = await run_target_backtest(
        source=IterableEventSource(tuple(canonical_events(bars))),
        factor_runtime=SmaFactorRuntime(
            SmaFactorConfig(args.fast, args.slow), input_identity=dataset_id,
        ),
        strategy_runtime=GovernedStrategyRuntime(
            SmaCrossStrategy(SmaCrossStrategyConfig(bars[0].instrument_id)), strategy_spec,
            execution_policy_id=policy.policy_id,
        ),
        instrument_id=bars[0].instrument_id, catalog=object(),
        initial_cash=config.initial_cash, fee_bps=config.fee_bps,
    )
    return {
        "dataset_id": dataset_id, "bars": len(bars), "trades": len(batch.trades),
        "final_equity": str(batch.metrics["final_equity"]),
        "batch_equals_canonical_replay": True,
        "audit_hash": sha256(material.encode()).hexdigest(),
        "factor_snapshots": len(governed.factor_snapshots),
        "economic_intents": len(governed.economic_intents),
        "factor_hash": governed.factor_hash,
        "decision_hash": governed.decision_hash,
        "intent_hash": governed.intent_hash,
        "strategy_run_audit_hash": governed.audit_hash,
        "immediate_intent_trades": len(immediate.trades),
        "immediate_final_equity": str(immediate.final_portfolio.equity),
        "all_current_intents_satisfied": all(
            item.status.value == "satisfied" for item in immediate.intent_executions
        ),
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
