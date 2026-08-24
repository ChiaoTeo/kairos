from __future__ import annotations

from kairospy.surface.workbench.screens.guided.models import (
    ArgumentPrompt,
    BusyPrompt,
    ConfirmationPrompt,
    GuidedSession,
    IdlePrompt,
    PromptMode,
)


def test_prompt_state_has_one_active_variant() -> None:
    session = GuidedSession()

    assert isinstance(session.prompt, IdlePrompt)
    assert session.prompt_mode is PromptMode.NAVIGATION
    assert session.pending_action is None

    session.ask("market")
    assert session.prompt == ArgumentPrompt("market")
    assert session.prompt_mode is PromptMode.ARGUMENT
    assert session.pending_action == "market"
    assert session.confirmation_prompt is None

    session.ask("resource:secret", secret=True)
    assert session.prompt == ArgumentPrompt("resource:secret", secret=True)
    assert session.prompt_mode is PromptMode.SECRET
    assert session.pending_action == "resource:secret"

    def operation() -> str:
        return "done"

    session.confirm("dangerous action", operation, "confirmed")
    assert session.prompt == ConfirmationPrompt(
        "dangerous action", operation, "confirmed"
    )
    assert session.prompt_mode is PromptMode.CONFIRMATION
    assert session.pending_action == "dangerous action"
    assert session.argument_prompt is None

    session.busy("confirmed")
    assert session.prompt == BusyPrompt("confirmed")
    assert session.prompt_mode is PromptMode.BUSY
    assert session.pending_action == "confirmed"

    session.finish_prompt()
    assert isinstance(session.prompt, IdlePrompt)
    assert session.prompt_mode is PromptMode.NAVIGATION


def test_finishing_prompt_preserves_records_but_navigation_reset_discards_them() -> None:
    session = GuidedSession(visible_records=("record",))
    session.ask("query")

    session.finish_prompt()
    assert session.visible_records == ("record",)

    session.reset_prompt()
    assert session.visible_records == ()


def test_worker_terminal_cleanup_is_owned_by_the_session() -> None:
    marker = object()
    session = GuidedSession(
        business_prompt=marker,
        order_prompt=marker,
        execution_prompt=marker,
        launch_market_prompt=marker,
        market_file_prompt=marker,
        project_prompt=marker,
        profile_action="create",
        workspace_market_prompt=marker,
    )

    for kind in (
        "business-result",
        "order-result",
        "execution-result",
        "launch-market-result",
        "market-file-result",
        "operations-project-result",
        "operations-profile-result",
        "workspace-market-result",
    ):
        session.clear_result_flow(kind)

    assert session.business_prompt is None
    assert session.order_prompt is None
    assert session.execution_prompt is None
    assert session.launch_market_prompt is None
    assert session.market_file_prompt is None
    assert session.project_prompt is None
    assert session.profile_action is None
    assert session.workspace_market_prompt is None
