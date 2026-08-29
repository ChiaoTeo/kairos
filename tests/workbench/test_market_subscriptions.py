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
    LIVE_MARKET_ACTIONS,
    WorkspaceMarketPromptState,
    equivalent_command,
    prompt_renderable,
    subscriptions_renderable,
)
from kairospy.surface.workbench.screens.operation import OperationSpec
from kairospy.surface.workbench.screens.results import ResultKind, ResultRoute
from kairospy.surface.workbench.screens.session import GuidedSession
from kairospy.surface.workbench.widgets import (
    ChoiceInteraction,
    InputInteraction,
    interaction_copy_text,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication


def _text(value: object) -> str:
    console = Console(width=160, record=True)
    console.print(value)
    return console.export_text()


def test_live_market_exposes_only_current_session_actions() -> None:
    actions = {action.id: action for action in LIVE_MARKET_ACTIONS}
    assert actions["session-subscriptions"].shortcut == "1"
    assert actions["subscribe"].shortcut == "2"
    assert actions["subscribe-custom"].shortcut == "3"
    assert actions["unsubscribe"].shortcut == "4"
    assert actions["snapshot"].shortcut == "5"
    assert actions["freshness"].shortcut == "6"
    assert not {
        "status",
        "routes",
        "subscriptions",
        "start",
        "stop",
        "restart",
        "logs",
        "pause",
        "resume",
    } & actions.keys()


def test_live_market_query_uses_user_task_title() -> None:
    state = SimpleNamespace(owner=None, dry_run=True, no_exec=True, yes=False)
    session = GuidedSession(context=("market", "live"))
    effects = runtime.handle_context(state, session, "1")

    assert effects is not None
    run = next(effect for effect in effects if isinstance(effect, RunOperation))
    assert run.operation.display_title == "我的实时行情"
    assert "服务" not in run.operation.display_title


def test_workspace_market_preview_is_attention_and_never_claims_runtime_health() -> (
    None
):
    session = GuidedSession(context=("market", "live"))
    prompt = WorkspaceMarketPromptState("subscribe")
    prompt.select_market("market:binance:spot:BTCUSDT", label="BTCUSDT")
    prompt.values["observations"] = "quote"
    session.market.workspace_prompt = prompt
    spec = OperationSpec.create(
        action_name="workspace.market.subscribe",
        audit_summary="添加 BTCUSDT 实时行情",
        display_title="添加实时行情",
        route=ResultRoute(ResultKind.WORKSPACE_MARKET),
        operation=lambda: None,
        running_status="正在执行",
    )

    effects = runtime.handle_success(
        SimpleNamespace(owner=None),
        session,
        spec,
        {"status": "preview", "action": "subscribe"},
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
        "subscribe", owner_id="operator:kairos-i:session-1", custom_content=True
    )
    for name, value in (
        ("market-id", "market:binance:spot:BTCUSDT"),
        ("observations", "quote,trade"),
        ("provider", ""),
    ):
        prompt.accept(name, value)
    assert prompt.owner_id == "operator:kairos-i:session-1"
    assert "owner_id" not in prompt.summary()


def test_workspace_market_prompt_uses_business_summary_not_request_json() -> None:
    prompt = WorkspaceMarketPromptState("subscribe")
    prompt.select_market(
        "market:binance:spot:BTCUSDT",
        label="BTCUSDT",
        description="binance · spot",
    )

    rendered = _text(prompt_renderable(prompt))

    assert "添加实时行情" in rendered
    assert "BTCUSDT · binance · spot" in rendered
    assert "实时报价" in rendered
    assert "由 Market 自动选择" in rendered
    assert "market:binance" not in rendered
    assert "'scope'" not in rendered
    assert "{" not in rendered


def test_add_realtime_market_starts_with_symbol_search_not_market_id() -> None:
    state = SimpleNamespace(owner=None, dry_run=True, no_exec=True, yes=False)
    session = GuidedSession(context=("market", "live"))

    effects = runtime.handle_context(state, session, "2")

    assert effects is not None
    interaction = next(
        effect.interaction for effect in effects if isinstance(effect, SetInteraction)
    )
    assert isinstance(interaction, InputInteraction)
    assert interaction.prompt == "输入代码或名称"
    copy = interaction_copy_text(interaction)
    assert "AAPL" in copy
    assert "Market ID" not in copy
    assert "scope" not in copy


def test_selected_reference_market_becomes_default_quote_subscription() -> None:
    from app_support import market as fixture_market

    state = SimpleNamespace(owner=None, dry_run=True, no_exec=True, yes=False)
    session = GuidedSession(context=("market", "live"))
    runtime.handle_context(state, session, "2")
    interaction = session.interaction
    assert isinstance(interaction, InputInteraction)
    search = runtime.handle_input(
        state,
        session,
        interaction.action,
        "AAPL",
    )
    assert search is not None
    search_operation = next(
        effect.operation for effect in search if isinstance(effect, RunOperation)
    )

    choices = runtime.handle_success(
        state, session, search_operation, (fixture_market(),)
    )
    assert choices is not None
    assert session.context == ("market", "workspace-market-results")

    run_effects = runtime.handle_context(state, session, "1")
    assert run_effects is not None
    run = next(effect for effect in run_effects if isinstance(effect, RunOperation))
    prompt = session.market.workspace_prompt
    assert isinstance(prompt, WorkspaceMarketPromptState)
    assert prompt.values["market-id"] == "market:aapl-nasdaq"
    assert prompt.values.get("observations", "quote") == "quote"
    assert run.operation.action_name == "workspace.market.subscribe"


def test_custom_subscription_content_is_a_business_choice() -> None:
    from app_support import market as fixture_market

    state = SimpleNamespace(owner=None, dry_run=True, no_exec=True, yes=False)
    session = GuidedSession(context=("market", "live"))
    session.market.selected = fixture_market()

    effects = runtime.handle_context(state, session, "3")

    assert effects is not None
    interaction = next(
        effect.interaction for effect in effects if isinstance(effect, SetInteraction)
    )
    assert isinstance(interaction, ChoiceInteraction)
    copy = interaction_copy_text(interaction)
    assert "实时报价与逐笔成交" in copy
    assert "quote,trade" not in copy
    assert "Observations" not in copy

    run_effects = runtime.handle_context(state, session, "2")
    assert run_effects is not None
    run = next(effect for effect in run_effects if isinstance(effect, RunOperation))
    prompt = session.market.workspace_prompt
    assert isinstance(prompt, WorkspaceMarketPromptState)
    assert prompt.values["observations"] == "quote,trade"
    assert prompt.values.get("provider", "") == ""
    assert run.operation.action_name == "workspace.market.subscribe"


def test_snapshot_resolves_one_provider_automatically() -> None:
    from app_support import market as fixture_market

    state = SimpleNamespace(owner=None, dry_run=False, no_exec=False, yes=False)
    session = GuidedSession(context=("market", "live"))
    session.market.selected = fixture_market()
    runtime.handle_context(state, session, "5")
    provider_effects = runtime.handle_context(state, session, "1")
    assert provider_effects is not None
    provider_run = next(
        effect.operation
        for effect in provider_effects
        if isinstance(effect, RunOperation)
    )
    assert provider_run.route.qualifier == "provider-options"

    run_effects = runtime.handle_success(
        state,
        session,
        provider_run,
        (
            {
                "provider": "massive",
                "state": "ready",
                "selected": False,
                "observation_kinds": ["quote"],
            },
        ),
    )

    assert run_effects is not None
    run = next(effect for effect in run_effects if isinstance(effect, RunOperation))
    prompt = session.market.workspace_prompt
    assert isinstance(prompt, WorkspaceMarketPromptState)
    assert prompt.values["provider"] == "massive"
    assert run.operation.action_name == "workspace.market.snapshot"


def test_snapshot_asks_for_provider_only_when_routes_are_ambiguous() -> None:
    from app_support import market as fixture_market

    state = SimpleNamespace(owner=None, dry_run=False, no_exec=False, yes=False)
    session = GuidedSession(context=("market", "live"))
    prompt = WorkspaceMarketPromptState("snapshot")
    prompt.select_market(
        str(fixture_market().id), label="AAPL", description="nasdaq · equity"
    )
    prompt.accept("kind", "quote")
    session.market.workspace_prompt = prompt
    spec = OperationSpec.create(
        action_name="workspace.market.snapshot.providers",
        audit_summary="选择数据来源",
        route=ResultRoute(ResultKind.WORKSPACE_MARKET, "provider-options"),
        operation=lambda: None,
        running_status="正在查找",
    )

    effects = runtime.handle_success(
        state,
        session,
        spec,
        (
            {
                "provider": "massive",
                "state": "ready",
                "selected": False,
                "observation_kinds": ["quote"],
            },
            {
                "provider": "sip",
                "state": "ready",
                "selected": False,
                "observation_kinds": ["quote"],
            },
        ),
    )

    assert effects is not None
    interaction = next(
        effect.interaction for effect in effects if isinstance(effect, SetInteraction)
    )
    assert isinstance(interaction, ChoiceInteraction)
    assert session.context == ("market", "workspace-providers")
    copy = interaction_copy_text(interaction)
    assert "massive" in copy
    assert "sip" in copy
    assert "Market ID" not in copy


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
    assert "当前关注" in rendered
    assert "BTCUSDT" in rendered
    assert "实时报价" in rendered
    assert "market:binance:spot" not in rendered
    assert "sub-1" not in rendered


def test_unsubscribe_selects_current_session_market_not_subscription_id() -> None:
    state = SimpleNamespace(owner=None, dry_run=True, no_exec=True, yes=False)
    session = GuidedSession(context=("market", "live"))
    runtime.handle_context(state, session, "4")
    prompt = session.market.workspace_prompt
    assert isinstance(prompt, WorkspaceMarketPromptState)
    spec = OperationSpec.create(
        action_name="workspace.market.unsubscribe.options",
        audit_summary="读取当前会话行情",
        route=ResultRoute(ResultKind.WORKSPACE_MARKET, "unsubscribe-options"),
        operation=lambda: None,
        running_status="正在读取",
    )

    effects = runtime.handle_success(
        state,
        session,
        spec,
        {
            "subscriptions": [
                {
                    "subscription_id": "sub-secret",
                    "_market_label": "BTCUSDT",
                    "_market_description": "binance · 现货 · 实时报价",
                }
            ]
        },
    )

    assert effects is not None
    interaction = next(
        effect.interaction for effect in effects if isinstance(effect, SetInteraction)
    )
    copy = interaction_copy_text(interaction)
    assert "BTCUSDT" in copy
    assert "sub-secret" not in copy
    assert "Subscription ID" not in copy


def test_workspace_market_failure_keeps_details_in_activity_only() -> None:
    session = GuidedSession(context=("market", "live"))
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
