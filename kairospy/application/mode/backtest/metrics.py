from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import sqrt

from .result import BacktestMetrics, ClosedTrade, EquityPoint


@dataclass(frozen=True, slots=True)
class MetricsModel:
    risk_free_rate: Decimal = Decimal("0")
    annualization_periods: Decimal | None = None

    def evaluate(
        self,
        equity_curve: tuple[EquityPoint, ...],
        trades: tuple[ClosedTrade, ...],
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


def _max_drawdown(equity_curve: tuple[EquityPoint, ...]) -> tuple[Decimal, Decimal]:
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
    equity_curve: tuple[EquityPoint, ...],
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


__all__ = ["MetricsModel"]
