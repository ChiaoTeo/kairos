from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from math import sqrt

from kairospy.application.domain.execution import SimulatedClosedTrade, SimulatedEquityPoint, SimulatedFill
from kairospy.core.order import OrderSide


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    trade_count: int
    win_count: int
    loss_count: int
    win_rate: Decimal
    gross_profit: Decimal
    gross_loss: Decimal
    net_profit: Decimal
    max_drawdown: Decimal
    max_drawdown_pct: Decimal
    sharpe: Decimal


@dataclass(frozen=True, slots=True)
class MetricsModel:
    risk_free_rate: Decimal = Decimal("0")
    annualization_periods: Decimal | None = None

    def evaluate(
        self,
        equity_curve: tuple[SimulatedEquityPoint, ...],
        trades: tuple[SimulatedClosedTrade, ...],
        *,
        initial_equity: Decimal,
    ) -> BacktestMetrics:
        net_profit = (equity_curve[-1].equity - initial_equity) if equity_curve else Decimal("0")
        gross_profit = sum((trade.net_pnl for trade in trades if trade.net_pnl > 0), Decimal("0"))
        gross_loss = sum((trade.net_pnl for trade in trades if trade.net_pnl < 0), Decimal("0"))
        win_count = sum(1 for trade in trades if trade.net_pnl > 0)
        loss_count = sum(1 for trade in trades if trade.net_pnl < 0)
        trade_count = len(trades)
        drawdown, drawdown_pct = _max_drawdown(equity_curve)
        return BacktestMetrics(
            trade_count=trade_count,
            win_count=win_count,
            loss_count=loss_count,
            win_rate=Decimal("0") if trade_count == 0 else Decimal(win_count) / Decimal(trade_count),
            gross_profit=gross_profit,
            gross_loss=gross_loss,
            net_profit=net_profit,
            max_drawdown=drawdown,
            max_drawdown_pct=drawdown_pct,
            sharpe=_sharpe(equity_curve, self.risk_free_rate, self.annualization_periods),
        )


def equity_point_from_account_view(time: datetime | None, account_view: object | None) -> SimulatedEquityPoint | None:
    if account_view is None or time is None:
        return None
    equity = getattr(account_view, "equity", None)
    cash = getattr(account_view, "cash", None)
    if equity is None or cash is None:
        return None
    positions = tuple(
        sorted(
            (str(getattr(position, "instrument_id")), Decimal(str(getattr(position, "quantity"))))
            for position in tuple(getattr(account_view, "positions", ()) or ())
        )
    )
    return SimulatedEquityPoint(time, Decimal(str(equity)), Decimal(str(cash)), positions)


def closed_trades_from_fills(fills: tuple[SimulatedFill, ...]) -> tuple[SimulatedClosedTrade, ...]:
    trades: list[SimulatedClosedTrade] = []
    open_trades: dict[str, _OpenTrade] = {}
    for fill in fills:
        current = open_trades.get(fill.instrument_id)
        if fill.side is OrderSide.BUY:
            if current is None:
                open_trades[fill.instrument_id] = _OpenTrade(fill.instrument_id, fill.occurred_at, fill.quantity, fill.price, fill.fee)
                continue
            total_quantity = current.quantity + fill.quantity
            total_cost = current.quantity * current.entry_price + fill.quantity * fill.price
            open_trades[fill.instrument_id] = _OpenTrade(
                fill.instrument_id,
                current.opened_at,
                total_quantity,
                total_cost / total_quantity,
                current.fees + fill.fee,
            )
            continue
        if current is None:
            continue
        close_quantity = min(fill.quantity, current.quantity)
        opening_fee = current.fees * close_quantity / current.quantity
        closing_fee = fill.fee * close_quantity / fill.quantity
        trades.append(
            SimulatedClosedTrade(
                fill.instrument_id,
                current.opened_at,
                fill.occurred_at,
                close_quantity,
                current.entry_price,
                fill.price,
                (fill.price - current.entry_price) * close_quantity,
                opening_fee + closing_fee,
            )
        )
        remaining = current.quantity - close_quantity
        if remaining == 0:
            del open_trades[fill.instrument_id]
            continue
        open_trades[fill.instrument_id] = _OpenTrade(
            fill.instrument_id,
            current.opened_at,
            remaining,
            current.entry_price,
            current.fees - opening_fee,
        )
    return tuple(trades)


@dataclass(frozen=True, slots=True)
class _OpenTrade:
    instrument_id: str
    opened_at: datetime
    quantity: Decimal
    entry_price: Decimal
    fees: Decimal


def _max_drawdown(equity_curve: tuple[SimulatedEquityPoint, ...]) -> tuple[Decimal, Decimal]:
    peak: Decimal | None = None
    max_drawdown = Decimal("0")
    max_drawdown_pct = Decimal("0")
    for point in equity_curve:
        if peak is None or point.equity > peak:
            peak = point.equity
        if peak is None or peak <= 0:
            continue
        drawdown = peak - point.equity
        drawdown_pct = drawdown / peak
        if drawdown > max_drawdown:
            max_drawdown = drawdown
        if drawdown_pct > max_drawdown_pct:
            max_drawdown_pct = drawdown_pct
    return max_drawdown, max_drawdown_pct


def _sharpe(
    equity_curve: tuple[SimulatedEquityPoint, ...],
    risk_free_rate: Decimal,
    annualization_periods: Decimal | None,
) -> Decimal:
    returns = []
    for previous, current in zip(equity_curve, equity_curve[1:]):
        if previous.equity != 0:
            returns.append((current.equity - previous.equity) / previous.equity)
    if len(returns) < 2:
        return Decimal("0")
    excess = [float(item - risk_free_rate) for item in returns]
    mean = sum(excess) / len(excess)
    variance = sum((item - mean) ** 2 for item in excess) / (len(excess) - 1)
    if variance == 0:
        return Decimal("0")
    ratio = mean / sqrt(variance)
    if annualization_periods is not None:
        ratio *= sqrt(float(annualization_periods))
    return Decimal(str(ratio))


__all__ = [
    "BacktestMetrics",
    "MetricsModel",
    "closed_trades_from_fills",
    "equity_point_from_account_view",
]
