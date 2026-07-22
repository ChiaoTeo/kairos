from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from kairospy.runtime.kernel import StrategyRunResult


@dataclass(frozen=True,slots=True)
class SignalAttribution:
    decisions:int
    economic_intents:int
    active_decisions:int
    factor_hash:str


@dataclass(frozen=True,slots=True)
class PortfolioAttribution:
    starting_equity:Decimal
    ending_equity:Decimal
    total_pnl:Decimal


@dataclass(frozen=True,slots=True)
class ExecutionAttribution:
    orders:int
    fills:int
    fees:Decimal
    slippage:Decimal


@dataclass(frozen=True,slots=True)
class RunAttribution:
    signal:SignalAttribution
    portfolio:PortfolioAttribution
    execution:ExecutionAttribution
    limitations:tuple[str,...]=(
        "Signal contribution requires a counterfactual benchmark and is not inferred from total PnL.",
        "Execution attribution reports observed fees/slippage; market impact remains model-dependent.",
    )


def build_run_attribution(result:StrategyRunResult,*,starting_equity:Decimal,ending_equity:Decimal,
                          orders:int,fills:int,fees:Decimal=Decimal("0"),slippage:Decimal=Decimal("0"))->RunAttribution:
    active=sum(item.action not in {"hold","wait","warmup","skip"} for item in result.decisions)
    return RunAttribution(SignalAttribution(len(result.decisions),len(result.economic_intents),active,result.factor_hash),
        PortfolioAttribution(starting_equity,ending_equity,ending_equity-starting_equity),
        ExecutionAttribution(orders,fills,fees,slippage))
