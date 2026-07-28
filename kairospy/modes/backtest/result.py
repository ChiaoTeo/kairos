from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from kairospy.core.account import AccountContext, AccountCurrentView
from kairospy.core.execution import ExecutionCoordinator
from kairospy.service.domains.execution import SimulatedFill
from kairospy.runtime import StrategyRunResult


@dataclass(frozen=True, slots=True)
class EquityPoint:
    time: datetime
    equity: Decimal
    cash: Decimal
    positions: tuple[tuple[str, Decimal], ...]


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    instrument_id: str
    opened_at: datetime
    closed_at: datetime
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    gross_pnl: Decimal
    fees: Decimal

    @property
    def net_pnl(self) -> Decimal:
        return self.gross_pnl - self.fees

    @property
    def return_pct(self) -> Decimal:
        basis = self.quantity * self.entry_price
        return Decimal("0") if basis == 0 else self.net_pnl / basis


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
class BacktestResult:
    account: AccountContext
    initial_equity: Decimal
    runtime: StrategyRunResult
    equity_curve: tuple[EquityPoint, ...]
    fills: tuple[SimulatedFill, ...]
    trades: tuple[ClosedTrade, ...]
    metrics: BacktestMetrics
    coordinator: ExecutionCoordinator
    account_view: AccountCurrentView | None = None

    @property
    def final_equity(self) -> Decimal:
        return self.equity_curve[-1].equity if self.equity_curve else Decimal("0")

    @property
    def net_profit(self) -> Decimal:
        return self.final_equity - self.initial_equity

    @property
    def total_return(self) -> Decimal:
        return Decimal("0") if self.initial_equity == 0 else self.net_profit / self.initial_equity


__all__ = [
    "BacktestMetrics",
    "BacktestResult",
    "ClosedTrade",
    "EquityPoint",
    "SimulatedFill",
]
