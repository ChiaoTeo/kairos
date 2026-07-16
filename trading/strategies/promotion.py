from __future__ import annotations

from dataclasses import dataclass

from trading.domain.strategy_contract import StrategyLifecycle


@dataclass(frozen=True,slots=True)
class PromotionGateDecision:
    passed: bool
    target: StrategyLifecycle
    reasons: tuple[str,...]


def evaluate_promotion_artifacts(target: StrategyLifecycle,results: tuple[dict,...]) -> PromotionGateDecision:
    reasons=[]
    states=[value.get("state",{}) for value in results]
    if target is StrategyLifecycle.RESEARCH_VALIDATED:
        if not any(state.get("maximum_level",0)>=2 and state.get("signal_status")=="SUPPORTED" for state in states):
            reasons.append("research promotion requires supported L2 signal evidence")
    elif target is StrategyLifecycle.TRADE_PROXY_VALIDATED:
        if not any(state.get("maximum_level",0)>=3 and state.get("strategy_status") in ("TRADE_PROXY_ONLY","SUPPORTED") for state in states):
            reasons.append("trade-proxy promotion requires L3 mapping evidence")
    elif target is StrategyLifecycle.EXECUTABLE_BACKTEST_VALIDATED:
        if not any(state.get("maximum_level",0)>=4 and state.get("execution_status")=="SUPPORTED" and state.get("strategy_status")=="SUPPORTED" for state in states):
            reasons.append("executable promotion requires supported L4 strategy and execution evidence")
    elif target is StrategyLifecycle.ROBUSTNESS_VALIDATED:
        if not any(state.get("maximum_level",0)>=5 and value.get("out_of_sample")=="decision_oos" for state,value in zip(states,results)):
            reasons.append("robustness promotion requires decision-OOS L5 evidence")
    elif target is StrategyLifecycle.PAPER_APPROVED:
        if not any(state.get("maximum_level",0)>=5 for state in states):reasons.append("paper approval requires L5 evidence")
    elif target in (StrategyLifecycle.LIVE_LIMITED,StrategyLifecycle.LIVE_APPROVED):
        if not any(state.get("maximum_level",0)>=6 for state in states):reasons.append("live promotion requires L6 paper or live evidence")
    else:reasons.append("target is not an evidence promotion stage")
    return PromotionGateDecision(not reasons,target,tuple(reasons))
