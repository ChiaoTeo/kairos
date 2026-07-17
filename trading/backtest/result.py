from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from trading.domain.intent import Intent
from trading.domain.order import Fill, Order, Settlement
from trading.strategies.base import StrategyDecision
from trading.backtest.portfolio import PortfolioSnapshot
from trading.risk.engine import RiskDecision


class ResultStatus(StrEnum):
    VALID = "valid"
    PARTIAL = "partial"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    start: datetime
    end: datetime
    initial_cash: Decimal = Decimal("100000")
    fill_model: str = "conservative"
    commission_per_contract: Decimal = Decimal("0.65")
    regulatory_fee_per_contract: Decimal = Decimal("0.03")
    random_seed: int = 7
    force_close_at_end: bool = True
    minimum_data_coverage: Decimal = Decimal("0.95")


@dataclass(frozen=True, slots=True)
class EquityPoint:
    timestamp: datetime
    equity_mid: Decimal
    equity_liquidation: Decimal
    cash: Decimal
    drawdown: Decimal
    delta: Decimal | None
    gamma: Decimal | None
    theta: Decimal | None
    vega: Decimal | None
    priced: bool


@dataclass(frozen=True, slots=True)
class BacktestResult:
    run_id: UUID
    strategy_id: str
    dataset_id: str
    config: BacktestConfig
    status: ResultStatus
    validity_reasons: tuple[str, ...]
    intents: tuple[Intent, ...]
    risk_decisions: tuple[RiskDecision, ...]
    orders: tuple[Order, ...]
    fills: tuple[Fill, ...]
    settlements: tuple[Settlement, ...]
    portfolio_snapshots: tuple[PortfolioSnapshot, ...]
    equity: tuple[EquityPoint, ...]
    strategy_decisions: tuple[StrategyDecision, ...]
    metrics: dict[str, object]
