from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StopReason(StrEnum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    CRASH = "crash"
    RISK_BREACH = "risk_breach"
    EMERGENCY = "emergency"


class StopAction(StrEnum):
    KEEP_POSITIONS = "keep_positions"
    CANCEL_ORDERS = "cancel_orders"
    REDUCE_ONLY = "reduce_only"
    FLATTEN = "flatten"

    @property
    def cancels_working_orders(self) -> bool:
        return self in {StopAction.CANCEL_ORDERS, StopAction.REDUCE_ONLY, StopAction.FLATTEN}

    @property
    def requires_reduce_only(self) -> bool:
        return self in {StopAction.REDUCE_ONLY, StopAction.FLATTEN}


@dataclass(frozen=True, slots=True)
class StopRule:
    reason: StopReason | str
    action: StopAction | str

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", StopReason(self.reason))
        object.__setattr__(self, "action", StopAction(self.action))


@dataclass(frozen=True, slots=True)
class StopPolicy:
    rules: tuple[StopRule, ...]
    policy_id: str = "strategy-default-stop-policy-v1"

    def __post_init__(self) -> None:
        if not self.policy_id.strip():
            raise ValueError("stop policy id is required")
        normalized = tuple(rule if isinstance(rule, StopRule) else StopRule(*rule) for rule in self.rules)
        reasons = tuple(rule.reason for rule in normalized)
        if len(set(reasons)) != len(reasons):
            raise ValueError("stop policy contains duplicate reasons")
        object.__setattr__(self, "rules", normalized)

    @classmethod
    def conservative(cls, *, policy_id: str = "strategy-default-stop-policy-v1") -> "StopPolicy":
        return cls(
            (
                StopRule(StopReason.MANUAL, StopAction.CANCEL_ORDERS),
                StopRule(StopReason.SCHEDULED, StopAction.CANCEL_ORDERS),
                StopRule(StopReason.CRASH, StopAction.CANCEL_ORDERS),
                StopRule(StopReason.RISK_BREACH, StopAction.REDUCE_ONLY),
                StopRule(StopReason.EMERGENCY, StopAction.REDUCE_ONLY),
            ),
            policy_id,
        )

    def action_for(self, reason: StopReason | str) -> StopAction:
        target = StopReason(reason)
        for rule in self.rules:
            if rule.reason is target:
                return rule.action
        raise LookupError(f"stop policy has no action for reason: {target.value}")


__all__ = [
    "StopAction",
    "StopPolicy",
    "StopReason",
    "StopRule",
]
