from __future__ import annotations

from collections.abc import Callable

from kairospy.surface.workbench.screens.guided.models import (
    GuidedSession,
    MarketSession,
    OperationsSession,
    ResourcesSession,
    StrategySession,
)
from kairospy.surface.workbench.screens.operation import OperationSpec
from kairospy.surface.workbench.screens.results import ResultKind, ResultRoute
from kairospy.surface.workbench.widgets import (
    ActionToken,
    ChoiceInteraction,
    ConfirmInteraction,
    Feature,
    InputInteraction,
    InteractionMode,
    RunningInteraction,
)


def _operation(summary: str, operation: Callable[[], object]) -> OperationSpec:
    return OperationSpec.create(
        action_name=summary,
        audit_summary=summary,
        route=ResultRoute(ResultKind.CONFIRMED),
        operation=operation,
        running_status=f"正在执行：{summary}",
    )


def test_prompt_state_has_one_active_variant() -> None:
    session = GuidedSession()

    assert isinstance(session.interaction, ChoiceInteraction)
    assert session.interaction.mode is InteractionMode.CHOICE
    assert session.interaction.mode is InteractionMode.CHOICE

    market = ActionToken(Feature.MARKET, "search")
    session.ask(market)
    assert session.interaction.mode is InteractionMode.INPUT
    assert session.interaction == InputInteraction(market, "search", "", "")

    secret = ActionToken(Feature.RESOURCES, "setup-secret")
    session.ask(secret, secret=True)
    assert session.interaction.mode is InteractionMode.INPUT
    assert isinstance(session.interaction, InputInteraction)
    assert session.interaction.action == secret
    assert session.interaction.secret

    def operation() -> str:
        return "done"

    spec = _operation("dangerous action", operation)
    session.confirm(spec)
    assert session.interaction.mode is InteractionMode.CONFIRM
    assert isinstance(session.interaction, ConfirmInteraction)
    assert session.interaction.operation is spec

    session.busy(spec.route)
    assert session.interaction.mode is InteractionMode.RUNNING
    assert isinstance(session.interaction, RunningInteraction)
    assert session.interaction.route == spec.route

    session.finish_prompt()
    assert isinstance(session.interaction, ChoiceInteraction)
    assert session.interaction.mode is InteractionMode.CHOICE


def test_confirmation_temporarily_overlays_and_restores_interaction() -> None:
    session = GuidedSession()
    session.choose((), title="已选市场")
    previous = session.interaction

    session.confirm(
        _operation("是否退出？", lambda: None),
        title="退出 Workbench",
    )

    assert isinstance(session.interaction, ConfirmInteraction)
    assert session.suspended_interaction is previous

    session.finish_prompt()

    assert session.interaction is previous
    assert session.suspended_interaction is None


def test_finishing_prompt_preserves_records_but_navigation_reset_discards_them() -> (
    None
):
    session = GuidedSession(visible_records=("record",))
    session.ask(ActionToken(Feature.GLOBAL, "query"))

    session.finish_prompt()
    assert session.visible_records == ("record",)

    session.reset_prompt()
    assert session.visible_records == ()


def test_worker_terminal_cleanup_is_owned_by_the_session() -> None:
    marker = object()
    session = GuidedSession(
        market=MarketSession(file_prompt=marker, workspace_prompt=marker),
        operations=OperationsSession(
            business_prompt=marker,
            project_prompt=marker,
            profile_action="create",
        ),
        resources=ResourcesSession(order_prompt=marker),
        strategy=StrategySession(
            execution_prompt=marker,
            launch_market_prompt=marker,
        ),
    )

    for kind in (
        ResultKind.BUSINESS,
        ResultKind.ORDER,
        ResultKind.EXECUTION,
        ResultKind.LAUNCH_MARKET,
        ResultKind.MARKET_FILE,
        ResultKind.OPERATIONS_PROJECT,
        ResultKind.OPERATIONS_PROFILE,
        ResultKind.WORKSPACE_MARKET,
    ):
        session.clear_result_flow(kind)

    assert session.operations.business_prompt is None
    assert session.resources.order_prompt is None
    assert session.strategy.execution_prompt is None
    assert session.strategy.launch_market_prompt is None
    assert session.market.file_prompt is None
    assert session.operations.project_prompt is None
    assert session.operations.profile_action is None
    assert session.market.workspace_prompt is None
