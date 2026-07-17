from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from math import sqrt
from statistics import mean, pstdev

from trading.domain.market_data import Bar
from trading.market_data.projections import CanonicalBarSeriesProjection
from trading.market_data.stream import EventSource
from trading.contracts import CanonicalEventEnvelope


@dataclass(frozen=True, slots=True)
class BarSeries:
    dataset_id: str
    bars: tuple[Bar, ...]

    def __post_init__(self) -> None:
        if not self.dataset_id.strip():
            raise ValueError("bar series dataset id cannot be empty")
        if any(current.start < previous.start for previous, current in zip(self.bars, self.bars[1:])):
            raise ValueError("bar series must be ordered by start time")


@dataclass(frozen=True, slots=True)
class SmaCrossConfig:
    fast_window: int = 20
    slow_window: int = 50
    initial_cash: Decimal = Decimal("100000")
    fee_bps: Decimal = Decimal("10")

    def __post_init__(self) -> None:
        if self.fast_window < 1 or self.slow_window <= self.fast_window:
            raise ValueError("SMA windows must satisfy 1 <= fast_window < slow_window")
        if self.initial_cash <= 0:
            raise ValueError("initial cash must be positive")
        if self.fee_bps < 0:
            raise ValueError("fee bps cannot be negative")


@dataclass(frozen=True, slots=True)
class SmaTrade:
    timestamp: datetime
    side: str
    price: Decimal
    quantity: Decimal
    fee: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class SmaEquityPoint:
    timestamp: datetime
    close: Decimal
    fast_sma: Decimal | None
    slow_sma: Decimal | None
    position: Decimal
    cash: Decimal
    equity: Decimal
    drawdown: Decimal


@dataclass(frozen=True, slots=True)
class SmaCrossResult:
    dataset_id: str
    config: SmaCrossConfig
    trades: tuple[SmaTrade, ...]
    equity: tuple[SmaEquityPoint, ...]
    metrics: dict[str, Decimal | int]

    def frame(self):
        try:
            import pandas as pd
        except ImportError as error:
            raise ImportError("pandas is required; install trader-research[notebook]") from error
        return pd.DataFrame(({
            "time": point.timestamp, "close": float(point.close),
            "fast_sma": float(point.fast_sma) if point.fast_sma is not None else None,
            "slow_sma": float(point.slow_sma) if point.slow_sma is not None else None,
            "position": float(point.position), "cash": float(point.cash),
            "equity": float(point.equity), "drawdown": float(point.drawdown),
        } for point in self.equity)).set_index("time")

    def trades_frame(self):
        try:
            import pandas as pd
        except ImportError as error:
            raise ImportError("pandas is required; install trader-research[notebook]") from error
        return pd.DataFrame(({
            "time": trade.timestamp, "side": trade.side, "price": float(trade.price),
            "quantity": float(trade.quantity), "fee": float(trade.fee), "reason": trade.reason,
        } for trade in self.trades)).set_index("time")


def backtest_sma_cross(dataset: BarSeries, config: SmaCrossConfig | None = None) -> SmaCrossResult:
    """Run a long-only SMA crossover with signals filled at the next bar open."""
    config = config or SmaCrossConfig()
    if len(dataset.bars) <= config.slow_window:
        raise ValueError("dataset needs more bars than the slow SMA window")
    closes = [bar.close for bar in dataset.bars]
    fast = _rolling_mean(closes, config.fast_window)
    slow = _rolling_mean(closes, config.slow_window)
    fee_rate = config.fee_bps / Decimal("10000")
    cash, position = config.initial_cash, Decimal("0")
    pending_position: bool | None = None
    trades: list[SmaTrade] = []
    equity: list[SmaEquityPoint] = []
    peak = config.initial_cash

    for index, bar in enumerate(dataset.bars):
        if pending_position is True and position == 0:
            quantity = cash / (bar.open * (Decimal("1") + fee_rate))
            notional = quantity * bar.open
            fee = notional * fee_rate
            cash -= notional + fee
            position = quantity
            trades.append(SmaTrade(bar.start, "buy", bar.open, quantity, fee, "fast_above_slow"))
        elif pending_position is False and position > 0:
            notional = position * bar.open
            fee = notional * fee_rate
            cash += notional - fee
            trades.append(SmaTrade(bar.start, "sell", bar.open, position, fee, "fast_below_slow"))
            position = Decimal("0")

        marked_equity = cash + position * bar.close
        peak = max(peak, marked_equity)
        equity.append(SmaEquityPoint(
            bar.end, bar.close, fast[index], slow[index], position, cash, marked_equity,
            marked_equity / peak - Decimal("1"),
        ))
        if fast[index] is not None and slow[index] is not None:
            pending_position = fast[index] > slow[index]

    if position > 0:
        last = dataset.bars[-1]
        notional = position * last.close
        fee = notional * fee_rate
        cash += notional - fee
        trades.append(SmaTrade(last.end, "sell", last.close, position, fee, "end_of_data"))
        position = Decimal("0")
        peak = max(peak, cash)
        final = equity[-1]
        equity[-1] = SmaEquityPoint(
            final.timestamp, final.close, final.fast_sma, final.slow_sma, position, cash, cash,
            cash / peak - Decimal("1"),
        )

    metrics = _metrics(tuple(equity), tuple(trades), config.initial_cash, fee_rate)
    return SmaCrossResult(dataset.dataset_id, config, tuple(trades), tuple(equity), metrics)


async def backtest_sma_cross_events(
    source: EventSource[CanonicalEventEnvelope],
    dataset_id: str,
    config: SmaCrossConfig | None = None,
) -> SmaCrossResult:
    """Run the governed SMA implementation from the shared asynchronous event port."""

    projection = CanonicalBarSeriesProjection()
    async for event in source.events():
        projection.apply(event)
    return backtest_sma_cross(BarSeries(dataset_id, tuple(projection.bars)), config)


def _rolling_mean(values: list[Decimal], window: int) -> list[Decimal | None]:
    result: list[Decimal | None] = []
    total = Decimal("0")
    for index, value in enumerate(values):
        total += value
        if index >= window:
            total -= values[index - window]
        result.append(total / Decimal(window) if index + 1 >= window else None)
    return result


def _metrics(equity: tuple[SmaEquityPoint, ...], trades: tuple[SmaTrade, ...], initial: Decimal, fee_rate: Decimal):
    final = equity[-1].equity
    periodic_returns = [
        float(current.equity / previous.equity - Decimal("1"))
        for previous, current in zip(equity, equity[1:]) if previous.equity
    ]
    seconds = (equity[-1].timestamp - equity[0].timestamp).total_seconds() / max(1, len(equity) - 1)
    periods_per_year = 365.25 * 86400 / seconds if seconds > 0 else 0
    deviation = pstdev(periodic_returns) if len(periodic_returns) > 1 else 0
    sharpe = mean(periodic_returns) / deviation * sqrt(periods_per_year) if deviation and periods_per_year else 0
    elapsed_years = Decimal(str((equity[-1].timestamp - equity[0].timestamp).total_seconds() / (365.25 * 86400)))
    annualized = (final / initial) ** (Decimal("1") / elapsed_years) - Decimal("1") if elapsed_years > 0 else Decimal("0")
    first_price, last_price = equity[0].close, equity[-1].close
    benchmark = (last_price / first_price) * (Decimal("1") - fee_rate) / (Decimal("1") + fee_rate) - Decimal("1")
    return {
        "initial_equity": initial,
        "final_equity": final,
        "total_return": final / initial - Decimal("1"),
        "annualized_return": annualized,
        "max_drawdown": min(point.drawdown for point in equity),
        "sharpe": Decimal(str(sharpe)),
        "trade_count": len(trades),
        "commissions": sum((trade.fee for trade in trades), Decimal("0")),
        "buy_and_hold_return": benchmark,
    }
