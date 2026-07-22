from __future__ import annotations

from dataclasses import dataclass

from kairospy.governance.stop_resolver import StopDecision, resolve_stop_policy
from kairospy.identity import AccountRef
from kairospy.runtime.application import KairosApplication, RuntimeStatus
from kairospy.runtime.clock import Clock, SystemClock
from kairospy.runtime.coordinator import ExecutionCoordinator
from kairospy.strategy.contracts import StrategySpec
from kairospy.strategy.stop_policy import StopAction, StopReason


@dataclass(frozen=True, slots=True)
class StopExecutionReport:
    strategy_id: str
    reason: StopReason
    requested_action: StopAction
    action: StopAction
    policy_id: str
    decision_explanation: str
    reduce_only_applied: bool
    cancelled_client_order_ids: tuple[str, ...]
    cancellation_failures: tuple[tuple[str, str], ...]
    flatten_requires_manual_approval: bool

    @classmethod
    def from_decision(
        cls,
        decision: StopDecision,
        *,
        reduce_only_applied: bool,
        cancelled_client_order_ids: tuple[str, ...] = (),
        cancellation_failures: tuple[tuple[str, str], ...] = (),
    ) -> "StopExecutionReport":
        return cls(
            decision.strategy_id,
            decision.reason,
            decision.requested_action,
            decision.action,
            decision.policy_id,
            decision.explanation,
            reduce_only_applied,
            cancelled_client_order_ids,
            cancellation_failures,
            decision.requested_action is StopAction.FLATTEN and decision.action is not StopAction.FLATTEN,
        )


class RuntimeStopController:
    STATE_KEY_PREFIX = "runtime_stop"
    LAST_STATE_KEY = "runtime_stop:last"

    def __init__(
        self,
        application: KairosApplication,
        coordinator: ExecutionCoordinator,
        strategy: StrategySpec,
        *,
        accounts: tuple[AccountRef, ...] | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.application = application
        self.coordinator = coordinator
        self.strategy = strategy
        self.accounts = application.accounts if accounts is None else accounts
        self.clock = clock or getattr(application, "clock", SystemClock())

    def execute(self, reason: StopReason | str, *, allow_flatten: bool = False) -> StopExecutionReport:
        decision = resolve_stop_policy(self.strategy, reason, allow_flatten=allow_flatten)
        reduce_only_applied = self._apply_reduce_only(decision)
        cancelled: list[str] = []
        failures: list[tuple[str, str]] = []
        if decision.cancels_working_orders:
            for account in self.accounts:
                result = self.coordinator.cancel_strategy_orders(
                    decision.strategy_id,
                    account,
                    f"stop:{decision.reason.value}:{decision.action.value}",
                )
                cancelled.extend(result.cancelled_client_order_ids)
                failures.extend(result.failures)
        report = StopExecutionReport.from_decision(
            decision,
            reduce_only_applied=reduce_only_applied,
            cancelled_client_order_ids=tuple(cancelled),
            cancellation_failures=tuple(failures),
        )
        at = self.clock.now()
        self.application.store.set_runtime_state(self._state_key(decision), report, at)
        self.application.store.set_runtime_state(self.LAST_STATE_KEY, report, at)
        return report

    def _apply_reduce_only(self, decision: StopDecision) -> bool:
        if not decision.requires_reduce_only:
            return False
        if self.application.status is RuntimeStatus.REDUCE_ONLY:
            return False
        if self.application.status not in {
            RuntimeStatus.READY,
            RuntimeStatus.RUNNING,
            RuntimeStatus.DEGRADED,
        }:
            return False
        self.application.degrade(
            f"stop policy {decision.reason.value}: {decision.action.value}",
            reduce_only=True,
        )
        return True

    def _state_key(self, decision: StopDecision) -> str:
        return f"{self.STATE_KEY_PREFIX}:{decision.strategy_id}:{decision.reason.value}"


__all__ = [
    "RuntimeStopController",
    "StopExecutionReport",
]
