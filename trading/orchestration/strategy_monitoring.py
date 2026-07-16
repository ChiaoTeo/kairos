from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class StrategyHealth(StrEnum):
    HEALTHY="healthy"
    DEGRADED="degraded"
    SUSPEND="suspend"


@dataclass(frozen=True,slots=True)
class StrategyMonitoringLimits:
    maximum_drawdown: Decimal
    maximum_execution_slippage_bps: Decimal
    minimum_fill_rate: Decimal
    maximum_feature_drift: Decimal
    maximum_data_staleness_seconds: int

    def __post_init__(self):
        if not Decimal("0")<self.maximum_drawdown<=Decimal("1"):raise ValueError("maximum drawdown must be in (0, 1]")
        if self.maximum_execution_slippage_bps<0 or not Decimal("0")<=self.minimum_fill_rate<=Decimal("1"):
            raise ValueError("invalid slippage or fill-rate limit")


@dataclass(frozen=True,slots=True)
class StrategyMonitoringSnapshot:
    strategy_id: str
    drawdown: Decimal
    execution_slippage_bps: Decimal
    fill_rate: Decimal
    feature_drift: Decimal
    data_staleness_seconds: int
    reconciliation_breaks: int=0


@dataclass(frozen=True,slots=True)
class StrategyHealthDecision:
    health: StrategyHealth
    capital_multiplier: Decimal
    reasons: tuple[str,...]


class StrategyHealthMonitor:
    def evaluate(self,snapshot: StrategyMonitoringSnapshot,limits: StrategyMonitoringLimits) -> StrategyHealthDecision:
        severe=[];degraded=[]
        if abs(snapshot.drawdown)>=limits.maximum_drawdown:severe.append("drawdown_limit")
        if snapshot.reconciliation_breaks:severe.append("reconciliation_break")
        if snapshot.data_staleness_seconds>limits.maximum_data_staleness_seconds:severe.append("stale_data")
        if snapshot.execution_slippage_bps>limits.maximum_execution_slippage_bps:degraded.append("execution_slippage")
        if snapshot.fill_rate<limits.minimum_fill_rate:degraded.append("fill_rate")
        if snapshot.feature_drift>limits.maximum_feature_drift:degraded.append("feature_drift")
        if severe:return StrategyHealthDecision(StrategyHealth.SUSPEND,Decimal("0"),tuple(severe+degraded))
        if degraded:return StrategyHealthDecision(StrategyHealth.DEGRADED,Decimal("0.5"),tuple(degraded))
        return StrategyHealthDecision(StrategyHealth.HEALTHY,Decimal("1"),())
