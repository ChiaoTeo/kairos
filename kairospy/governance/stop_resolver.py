from __future__ import annotations

from dataclasses import dataclass

from kairospy.strategy.contracts import StrategySpec
from kairospy.strategy.stop_policy import StopAction, StopReason


_ACTION_RANK = {
    StopAction.KEEP_POSITIONS: 0,
    StopAction.CANCEL_ORDERS: 1,
    StopAction.REDUCE_ONLY: 2,
    StopAction.FLATTEN: 3,
}

_SYSTEM_FLOORS = {
    StopReason.CRASH: StopAction.CANCEL_ORDERS,
    StopReason.RISK_BREACH: StopAction.REDUCE_ONLY,
    StopReason.EMERGENCY: StopAction.REDUCE_ONLY,
}


@dataclass(frozen=True, slots=True)
class StopDecision:
    strategy_id: str
    reason: StopReason
    requested_action: StopAction
    action: StopAction
    policy_id: str
    explanation: str

    @property
    def cancels_working_orders(self) -> bool:
        return self.action.cancels_working_orders

    @property
    def requires_reduce_only(self) -> bool:
        return self.action.requires_reduce_only


def resolve_stop_policy(
    strategy: StrategySpec,
    reason: StopReason | str,
    *,
    allow_flatten: bool = False,
) -> StopDecision:
    stop_reason = StopReason(reason)
    requested = strategy.default_stop_policy.action_for(stop_reason)
    floor = _SYSTEM_FLOORS.get(stop_reason, StopAction.KEEP_POSITIONS)
    action = _more_restrictive(requested, floor)
    explanation = "strategy default policy accepted"
    if action is not requested:
        explanation = f"system floor upgraded {requested.value} to {action.value}"
    if action is StopAction.FLATTEN and not allow_flatten:
        action = StopAction.REDUCE_ONLY
        explanation = "flatten requires explicit runtime approval; downgraded to reduce_only"
    return StopDecision(
        strategy.strategy_id,
        stop_reason,
        requested,
        action,
        strategy.default_stop_policy.policy_id,
        explanation,
    )


def _more_restrictive(left: StopAction, right: StopAction) -> StopAction:
    return left if _ACTION_RANK[left] >= _ACTION_RANK[right] else right


__all__ = [
    "StopDecision",
    "resolve_stop_policy",
]
