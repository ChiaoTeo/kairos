from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from kairospy.core.account import AccountContext
from kairospy.core.execution import ExecutionCoordinator
from kairospy.application.runtime.projection.account import AccountCurrentView
from kairospy.application.service.domains.execution import SimulatedClosedTrade, SimulatedEquityPoint
from kairospy.application.service.domains.execution import SimulatedFill
from kairospy.application.runtime.model import StrategyRunResult


EquityPoint = SimulatedEquityPoint
ClosedTrade = SimulatedClosedTrade


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
