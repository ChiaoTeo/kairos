from __future__ import annotations

from kairospy.surface.workbench.screens.activity import ActivityOutcome
from kairospy.surface.workbench.screens.effects import AppendActivity, SetStatus
from kairospy.surface.workbench.screens.flows.launch import runtime
from kairospy.surface.workbench.screens.operation import OperationSpec
from kairospy.surface.workbench.screens.results import ResultKind, ResultRoute
from kairospy.surface.workbench.screens.session import GuidedSession

from app_support import workbench_state


def test_failed_backtest_wait_is_presented_as_failure_with_recovery_action() -> None:
    spec = OperationSpec.create(
        action_name="strategy.instance.wait",
        audit_summary="等待回测",
        route=ResultRoute(ResultKind.STRATEGY),
        operation=lambda: None,
        running_status="正在等待回测…",
    )

    effects = runtime.handle_success(
        workbench_state(),
        GuidedSession(),
        spec,
        {
            "status": "failed",
            "launch_id": "demo",
            "instance_id": "run-1",
            "failure_reason": "backtest finished without a report",
            "next_action": "kairos launch logs demo",
        },
    )

    assert effects is not None
    activity = next(effect for effect in effects if isinstance(effect, AppendActivity))
    status = next(effect for effect in effects if isinstance(effect, SetStatus))
    assert activity.activity.outcome is ActivityOutcome.FAILURE
    assert "Launch 结果失败" in str(activity.activity.copy_text)
    assert "backtest finished without a report" in str(activity.activity.copy_text)
    assert "kairos launch logs demo" in str(activity.activity.copy_text)
    assert status.message == "操作失败 · 请按结果提示恢复"
