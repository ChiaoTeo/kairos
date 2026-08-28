from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from kairospy.surface.workbench.screens.effects import (
    AppendActivity,
    RunOperation,
    SetInteraction,
)
from kairospy.surface.workbench.screens.activity import ActivityOutcome
from kairospy.surface.workbench.screens.flows.market import runtime
from kairospy.surface.workbench.screens.flows.market.workspace import (
    WORKSPACE_MARKET_ACTIONS,
    WorkspaceMarketPromptState,
    equivalent_command,
    subscriptions_renderable,
)
from kairospy.surface.workbench.screens.operation import OperationSpec
from kairospy.surface.workbench.screens.results import ResultKind, ResultRoute
from kairospy.surface.workbench.screens.session import GuidedSession
from kairospy.surface.workbench.widgets import ChoiceInteraction
from kairospy.system.apps.workspace.application import WorkspaceApplication


def _text(value: object) -> str:
    console = Console(width=160, record=True)
    console.print(value)
    return console.export_text()


def test_market_workspace_exposes_both_subscription_scopes_and_mutations() -> None:
    actions = {action.id: action for action in WORKSPACE_MARKET_ACTIONS}
    assert actions["session-subscriptions"].shortcut == "3"
    assert actions["subscriptions"].shortcut == "4"
    assert actions["subscribe"].shortcut == "s"
    assert actions["unsubscribe"].shortcut == "u"


def test_workspace_market_queries_use_product_titles_not_internal_actions() -> None:
    state = SimpleNamespace(owner=None, dry_run=True, no_exec=True, yes=False)

    for command, expected_title in (("1", "Market 运行状态"), ("2", "Market 数据路由")):
        session = GuidedSession(context=("market", "connected"))
        effects = runtime.handle_context(state, session, command)

        assert effects is not None
        run = next(effect for effect in effects if isinstance(effect, RunOperation))
        assert run.operation.display_title == expected_title
        assert run.operation.audit_summary.startswith("Workspace Market ")


def test_workspace_market_preview_is_attention_and_never_claims_runtime_health() -> (
    None
):
    session = GuidedSession(context=("market", "connected"))
    session.market.workspace_prompt = WorkspaceMarketPromptState("status")
    spec = OperationSpec.create(
        action_name="workspace.market.status",
        audit_summary="Workspace Market status",
        display_title="Market 运行状态",
        route=ResultRoute(ResultKind.WORKSPACE_MARKET),
        operation=lambda: None,
        running_status="正在执行",
    )

    effects = runtime.handle_success(
        SimpleNamespace(owner=None),
        session,
        spec,
        {"status": "preview", "action": "status"},
    )

    assert effects is not None
    activity = next(
        effect.activity for effect in effects if isinstance(effect, AppendActivity)
    )
    assert activity.outcome is ActivityOutcome.ATTENTION
    assert activity.copy_text is not None
    assert "未执行任何修改" in activity.copy_text
    assert "数据面均已就绪" not in activity.copy_text


def test_operator_subscription_prompt_keeps_platform_owner_outside_strategy_state() -> (
    None
):
    prompt = WorkspaceMarketPromptState(
        "subscribe", owner_id="operator:kairos-i:session-1"
    )
    for name, value in (
        ("market-id", "market:binance:spot:BTCUSDT"),
        ("observations", "quote,trade"),
        ("provider", ""),
    ):
        prompt.accept(name, value)
    assert prompt.owner_id == "operator:kairos-i:session-1"
    assert "owner_id" not in prompt.summary()


def test_subscription_inventory_rendering_shows_owner_and_pending_state() -> None:
    panel = subscriptions_renderable(
        {
            "subscriptions": [
                {
                    "subscription_id": "sub-1",
                    "owner_id": "operator:kairos-i:session-1",
                    "market_ids": ["market:binance:spot:BTCUSDT"],
                    "observations": ["quote"],
                    "selected_providers": ["binance"],
                    "state": "active",
                    "pending_reason": None,
                }
            ]
        },
        current_session=True,
    )
    rendered = _text(panel)
    assert "当前 Kairos I 订阅" in rendered
    assert "operator:kairos-i:session-1" in rendered
    assert "market:binance:spot:BTCUSDT" in rendered


def test_workspace_market_failure_keeps_details_in_activity_only() -> None:
    session = GuidedSession(context=("market", "connected"))
    spec = OperationSpec.create(
        action_name="workspace.market.snapshot",
        audit_summary="Workspace Market snapshot",
        route=ResultRoute(ResultKind.WORKSPACE_MARKET),
        operation=lambda: None,
        running_status="正在执行",
    )
    error = "required arguments were not provided: --launch-id --instance-id"

    effects = runtime.handle_failure(SimpleNamespace(owner=None), session, spec, error)

    assert effects is not None
    activity = next(effect for effect in effects if isinstance(effect, AppendActivity))
    interaction = next(
        effect for effect in effects if isinstance(effect, SetInteraction)
    )
    assert activity.activity.copy_text is not None
    assert activity.activity.copy_text.strip() == error
    assert isinstance(interaction.interaction, ChoiceInteraction)
    assert interaction.interaction.summary is None


def test_workspace_market_retry_uses_public_system_command(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "workspace", workspace_id="market-command"
    )
    prompt = WorkspaceMarketPromptState("snapshot")
    for name, value in (
        ("kind", "quote"),
        ("market-id", "market:binance:spot:BTCUSDT"),
        ("provider", "binance"),
    ):
        prompt.accept(name, value)

    command = equivalent_command(SimpleNamespace(owner=workspace), prompt)

    assert command is not None
    assert command[:5] == ("kairos", "system", "component", "market", "snapshot")
    assert "kairos-market-cli" not in command
    assert "--launch-id" not in command
    assert "--instance-id" not in command
