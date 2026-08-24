from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import threading
import tomllib
from types import SimpleNamespace
from typing import Any

import pytest

from kairospy.strategy.apps.agent.application.model_connections import (
    ModelProviderConnectionApplication,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication
from kairospy.investment.apps.reference.application.models import (
    Asset,
    InstrumentRef,
    Market,
    MarketStatus,
    ReferenceStatus,
)
from kairospy.primitives.reference import ExchangeId, InstrumentId, MarketId
from kairospy.surface.workbench import KairosWorkbenchApp, WorkbenchState
from kairospy.surface.workbench.dialogs import HelpDialog
from kairospy.surface.workbench.dialogs import ConfirmDialog
from kairospy.surface.workbench.screens.business_tools import (
    BusinessToolsScreen,
    CapitalToolsScreen,
    IntegrationCapabilitiesScreen,
    RiskToolsScreen,
)
from kairospy.surface.workbench.screens.execution import ExecutionSubmitScreen
from kairospy.surface.workbench.screens.market import (
    MarketDetailScreen,
    MarketHistoryScreen,
    MarketScreen,
)
from kairospy.surface.workbench.screens.launch_setup import LaunchSetupScreen
from kairospy.surface.workbench.screens.operations import (
    AdvancedConfigScreen,
    OperationsScreen,
    ProfileScreen,
    ProjectScreen,
)
from kairospy.surface.workbench.screens.observe import ObserveScreen
from kairospy.surface.workbench.screens.reference import (
    ReferenceDetailScreen,
    ReferenceScreen,
)
from kairospy.surface.workbench.screens.resource_setup import ResourceSetupScreen
from kairospy.surface.workbench.screens.research import ResearchScreen
from kairospy.surface.workbench.screens.resources import (
    ResourceDetailScreen,
    ResourcesScreen,
)
from kairospy.surface.workbench.screens.strategy import (
    LaunchDetailScreen,
    StrategyScreen,
)
from kairospy.surface.console.models import ObserveSnapshot
from kairospy.surface.workbench.widgets import ActionList
from textual.containers import Vertical
from textual.widgets import Button, DataTable, Input, Label, RichLog, Select


def _state() -> WorkbenchState:
    owner = SimpleNamespace(
        workspace_id="trader",
        paths=SimpleNamespace(
            root=Path("/workspace/trader/.kairos"),
            project_root=Path("/workspace/trader"),
        ),
    )
    return WorkbenchState(owner=owner, workspace_arg=owner.paths.root)


def _market() -> Market:
    return Market(
        id=MarketId("market:aapl-nasdaq"),
        instrument=InstrumentRef(InstrumentId("instrument:aapl"), "AAPL"),
        listing_id=None,
        exchange_id=ExchangeId("exchange:nasdaq"),
        instrument_kind="equity",
        venue_symbol="AAPL",
        quote_asset="USD",
        status=MarketStatus.ACTIVE,
    )


def test_home_is_one_textual_screen_with_six_product_actions() -> None:
    async def run() -> tuple[int, str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            actions = app.screen.query_one("#home-actions", ActionList)
            workspace = str(app.screen.query_one("#workspace-summary").render())
            return actions.option_count, app.screen.sub_title or "", workspace

    count, subtitle, workspace = asyncio.run(run())

    assert count == 6
    assert subtitle == "首页"
    assert "trader" in workspace


@pytest.mark.parametrize("theme", ("textual-dark", "textual-light"))
def test_home_renders_in_supported_terminal_themes(theme: str) -> None:
    async def run() -> int:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            app.theme = theme
            await pilot.pause()
            return app.screen.query_one("#home-actions", ActionList).option_count

    assert asyncio.run(run()) == 6


def test_home_renders_when_no_color_is_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")

    async def run() -> int:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(60, 20)) as pilot:
            await pilot.pause()
            return app.screen.query_one("#home-actions", ActionList).option_count

    assert asyncio.run(run()) == 6


def test_workspace_identity_is_visible_in_shared_header_context() -> None:
    async def run() -> str:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            return app.screen.title or ""

    assert asyncio.run(run()) == "Kairos · trader"


def test_home_numeric_shortcut_dispatches_selected_product() -> None:
    async def run() -> list[str]:
        app = KairosWorkbenchApp(_state())
        opened: list[str] = []
        app.open_section = opened.append  # type: ignore[method-assign]
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("1")
            await pilot.pause()
        return opened

    assert asyncio.run(run()) == ["market"]


def test_home_layout_runs_at_supported_terminal_sizes() -> None:
    async def run(size: tuple[int, int]) -> int:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            return app.screen.query_one("#home-actions", ActionList).option_count

    for size in ((60, 20), (80, 24), (120, 30), (160, 40)):
        assert asyncio.run(run(size)) == 6


def test_market_search_stays_inside_screen_and_opens_selected_market() -> None:
    async def run() -> tuple[int, str]:
        app = KairosWorkbenchApp(_state())
        original = MarketScreen._find_markets
        MarketScreen._find_markets = lambda self, query: (_market(),)  # type: ignore[method-assign]
        try:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.press("1")
                await pilot.pause()
                screen = app.screen
                assert isinstance(screen, MarketScreen)
                screen.action_search()
                search = screen.query_one("#market-search", Input)
                search.value = "AAPL"
                search.focus()
                await pilot.press("enter")
                await pilot.pause(0.1)
                table = screen.query_one("#market-results", DataTable)
                assert table.row_count == 1
                table.focus()
                await pilot.press("enter")
                await pilot.pause()
                return table.row_count, app.screen.sub_title or ""
        finally:
            MarketScreen._find_markets = original

    rows, subtitle = asyncio.run(run())

    assert rows == 1
    assert subtitle == "首页 › 市场行情 › AAPL"


def test_market_observation_uses_application_result_in_current_screen() -> None:
    async def run() -> str:
        app = KairosWorkbenchApp(_state())
        original_routes = MarketDetailScreen._load_routes
        original_observation = MarketDetailScreen._load_observation
        MarketDetailScreen._load_routes = (  # type: ignore[method-assign]
            lambda self, kind: ({"provider": "demo"},)
        )
        MarketDetailScreen._load_observation = (  # type: ignore[method-assign]
            lambda self, provider: {"symbol": "AAPL", "price": "226.50"}
        )
        try:
            async with app.run_test(size=(100, 30)) as pilot:
                app.push_screen(MarketDetailScreen(_market()))
                await pilot.pause()
                actions = app.screen.query_one("#observation-actions", ActionList)
                actions.focus()
                await pilot.press("enter")
                await pilot.pause(0.2)
                return str(app.screen.query_one("#observation-status", Label).render())
        finally:
            MarketDetailScreen._load_routes = original_routes
            MarketDetailScreen._load_observation = original_observation

    assert asyncio.run(run()) == "行情已更新"


def test_reference_asset_search_and_detail_stay_in_screen_stack() -> None:
    asset = Asset(
        id="asset:usd",
        code="USD",
        name="US Dollar",
        asset_class="currency",
        status=ReferenceStatus.ACTIVE,
    )

    async def run() -> tuple[int, str, str]:
        app = KairosWorkbenchApp(_state())
        original = ReferenceScreen._find_records
        ReferenceScreen._find_records = lambda self, query: (asset,)  # type: ignore[method-assign]
        try:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.press("2")
                await pilot.pause()
                screen = app.screen
                assert isinstance(screen, ReferenceScreen)
                screen._open_search("assets")
                search = screen.query_one("#reference-search", Input)
                search.value = "USD"
                search.focus()
                await pilot.press("enter")
                await pilot.pause(0.1)
                table = screen.query_one("#reference-results", DataTable)
                table.focus()
                await pilot.press("enter")
                await pilot.pause()
                assert isinstance(app.screen, ReferenceDetailScreen)
                return (
                    table.row_count,
                    app.screen.sub_title or "",
                    str(app.screen.query_one("#reference-detail").render()),
                )
        finally:
            ReferenceScreen._find_records = original

    rows, subtitle, detail = asyncio.run(run())

    assert rows == 1
    assert subtitle == "首页 › 市场标的 › USD"
    assert "asset:usd" in detail


def test_escape_returns_to_preserved_reference_results_and_focus() -> None:
    asset = Asset(
        id="asset:usd",
        code="USD",
        name="US Dollar",
        asset_class="currency",
        status=ReferenceStatus.ACTIVE,
    )

    async def run() -> tuple[bool, int, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = ReferenceScreen()
            app.push_screen(screen)
            await pilot.pause()
            screen._open_search("assets")
            screen._show_results((asset,))
            table = screen.query_one("#reference-results", DataTable)
            table.focus()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            return app.screen is screen, table.row_count, table.has_focus

    same_screen, rows, focused = asyncio.run(run())

    assert same_screen
    assert rows == 1
    assert focused


def test_all_home_products_open_real_screens_without_placeholder_routes() -> None:
    async def run(section: str) -> type[object]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            app.open_section(section)
            await pilot.pause()
            return type(app.screen)

    expected = {
        "market": MarketScreen,
        "reference": ReferenceScreen,
        "strategy": StrategyScreen,
        "resources": ResourcesScreen,
        "research": ResearchScreen,
        "operations": OperationsScreen,
    }
    for section, screen_type in expected.items():
        assert asyncio.run(run(section)) is screen_type


@pytest.mark.parametrize("size", ((60, 20), (80, 24), (120, 30), (160, 40)))
@pytest.mark.parametrize(
    ("section", "screen_type"),
    (
        ("market", MarketScreen),
        ("reference", ReferenceScreen),
        ("strategy", StrategyScreen),
        ("resources", ResourcesScreen),
        ("research", ResearchScreen),
        ("operations", OperationsScreen),
        ("observe", ObserveScreen),
    ),
)
def test_primary_screens_mount_at_supported_terminal_sizes(
    size: tuple[int, int], section: str, screen_type: type[object]
) -> None:
    async def run() -> type[object]:
        app = KairosWorkbenchApp(_state(), observe_refresh_seconds=3600)
        async with app.run_test(size=size) as pilot:
            app.open_section(section)
            await pilot.pause()
            return type(app.screen)

    assert asyncio.run(run()) is screen_type


def test_worker_error_is_rendered_inside_current_screen() -> None:
    original = OperationsScreen._execute
    OperationsScreen._execute = (  # type: ignore[method-assign]
        lambda self, action: (_ for _ in ()).throw(RuntimeError("socket unavailable"))
    )

    async def run() -> str:
        app = KairosWorkbenchApp(_state())
        try:
            async with app.run_test(size=(100, 30)) as pilot:
                screen = OperationsScreen()
                app.push_screen(screen)
                await pilot.pause()
                screen._run("doctor")
                await pilot.pause(0.2)
                return str(screen.query_one("#operations-status", Label).render())
        finally:
            OperationsScreen._execute = original  # type: ignore[method-assign]

    assert asyncio.run(run()) == "操作失败：socket unavailable"


def test_ctrl_c_cancels_worker_owned_by_current_screen() -> None:
    release = threading.Event()

    async def run() -> bool:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = OperationsScreen()
            app.push_screen(screen)
            await pilot.pause()
            worker = screen.run_worker(
                lambda: release.wait(2),
                name="cancellable",
                group="test-cancel",
                thread=True,
                exit_on_error=False,
            )
            await pilot.pause(0.05)
            await pilot.press("ctrl+c")
            await pilot.pause(0.05)
            release.set()
            return worker.is_cancelled

    assert asyncio.run(run())


def test_text_input_consumes_global_shortcuts_as_text() -> None:
    async def run() -> tuple[str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            app.open_section("market")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, MarketScreen)
            screen.action_search()
            search = screen.query_one("#market-search", Input)
            search.focus()
            await pilot.press("q", "1", "?")
            await pilot.pause()
            return search.value, app.is_running

    value, running = asyncio.run(run())

    assert value == "q1?"
    assert running


def test_help_is_contextual_modal_and_escape_returns_to_current_screen() -> None:
    async def run() -> tuple[bool, bool, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            home = app.screen
            await pilot.press("?")
            await pilot.pause()
            opened = isinstance(app.screen, HelpDialog)
            content = str(app.screen.query_one("#help-content").render())
            await pilot.press("escape")
            await pilot.pause()
            return opened, app.screen is home, content

    opened, returned, content = asyncio.run(run())

    assert opened
    assert returned
    assert "首页入口" in content
    assert "Ctrl+P" in content


def test_observe_recommendation_opens_launch_in_shared_screen_stack() -> None:
    snapshot = ObserveSnapshot(
        workspace_id="trader",
        components={},
        launches=(
            {
                "launch_id": "demo",
                "mode": "paper",
                "state": "degraded",
                "instance_id": "run-1",
                "updated_at": "2026-08-24T12:00:00Z",
            },
        ),
        observed_at=datetime.now(timezone.utc),
    )

    async def run() -> tuple[type[object], str | None]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = ObserveScreen(refresh_seconds=3600)
            app.push_screen(screen)
            await pilot.pause()
            screen._last = snapshot
            screen.action_next_step()
            await pilot.pause()
            return type(app.screen), app.state.selected_launch

    screen_type, selected = asyncio.run(run())

    assert screen_type is LaunchDetailScreen
    assert selected == "demo"


def test_resource_setup_keeps_secret_input_masked_and_escape_cancels() -> None:
    async def run() -> tuple[bool, bool]:
        app = KairosWorkbenchApp(_state())
        result: list[dict[str, object] | None] = []
        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(ResourceSetupScreen("data"), result.append)
            await pilot.pause()
            secret = app.screen.query_one("#secret-primary", Input)
            masked = secret.password
            await pilot.press("escape")
            await pilot.pause()
            return masked, result == [None]

    masked, cancelled = asyncio.run(run())

    assert masked
    assert cancelled


def test_resource_save_respects_dry_run_without_writing_or_exposing_secret() -> None:
    async def run() -> tuple[bool, str]:
        state = _state()
        state.dry_run = True
        app = KairosWorkbenchApp(state)
        async with app.run_test(size=(100, 30)) as pilot:
            screen = ResourceSetupScreen("data")
            app.push_screen(screen)
            await pilot.pause()
            screen.query_one("#secret-primary", Input).value = "should-not-appear"
            screen.query_one("#save", Button).press()
            await pilot.pause()
            message = str(screen.query_one("#resource-form-error", Label).render())
            return app.screen is screen, message

    same_screen, message = asyncio.run(run())

    assert same_screen
    assert message == "预览：保存市场数据；密钥不会显示或写入"
    assert "should-not-appear" not in message


def test_resource_save_uses_confirmation_and_existing_identity_is_locked() -> None:
    async def run() -> tuple[bool, bool]:
        app = KairosWorkbenchApp(_state())
        record = {
            "connection_id": "massive",
            "credential_id": "massive-readonly",
            "endpoint": "https://api.massive.com",
        }
        async with app.run_test(size=(100, 30)) as pilot:
            screen = ResourceSetupScreen("data", record)
            app.push_screen(screen)
            await pilot.pause()
            locked = screen.query_one("#resource-id", Input).disabled
            screen.query_one("#save", Button).press()
            await pilot.pause()
            return locked, isinstance(app.screen, ConfirmDialog)

    locked, confirmed = asyncio.run(run())

    assert locked
    assert confirmed


def test_operations_routes_project_and_advanced_config_inside_screen_stack() -> None:
    async def run(shortcut: str) -> type[object]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(OperationsScreen())
            await pilot.pause()
            actions = app.screen.query_one("#operations-actions", ActionList)
            assert actions.highlight_shortcut(shortcut)
            actions.action_select()
            await pilot.pause()
            return type(app.screen)

    assert asyncio.run(run("1")) is ProjectScreen
    assert asyncio.run(run("6")) is AdvancedConfigScreen
    assert asyncio.run(run("9")) is BusinessToolsScreen


def test_business_tools_open_risk_capital_and_integration_in_same_stack() -> None:
    async def run(shortcut: str) -> type[object]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(BusinessToolsScreen())
            await pilot.pause()
            actions = app.screen.query_one("#business-tool-actions", ActionList)
            assert actions.highlight_shortcut(shortcut)
            actions.action_select()
            await pilot.pause()
            return type(app.screen)

    assert asyncio.run(run("1")) is RiskToolsScreen
    assert asyncio.run(run("2")) is CapitalToolsScreen
    assert asyncio.run(run("3")) is IntegrationCapabilitiesScreen


def test_risk_standalone_action_calls_native_application_without_typer() -> None:
    from kairospy.system.apps.components.application import NativeCliApplication

    seen: list[tuple[str, list[str]]] = []
    original = NativeCliApplication.run
    NativeCliApplication.run = (  # type: ignore[method-assign]
        lambda self, component, arguments: (
            seen.append((component, arguments)) or {"status": "valid"}
        )
    )

    async def run() -> str:
        app = KairosWorkbenchApp(_state())
        try:
            async with app.run_test(size=(100, 30)) as pilot:
                screen = RiskToolsScreen()
                app.push_screen(screen)
                await pilot.pause()
                screen._run("preview", ["--policy-file", "p.json", "--request-file", "r.json"])
                await pilot.pause(0.2)
                return str(screen.query_one("#risk-tool-status", Label).render())
        finally:
            NativeCliApplication.run = original  # type: ignore[method-assign]

    assert asyncio.run(run()) == "操作完成"
    assert seen == [
        (
            "risk",
            [
                "standalone",
                "preview",
                "--policy-file",
                "p.json",
                "--request-file",
                "r.json",
            ],
        )
    ]


def test_capital_plan_collects_all_three_files() -> None:
    screen = CapitalToolsScreen()
    seen: list[tuple[str, list[str] | None]] = []
    screen._run = lambda action, arguments=None: seen.append(  # type: ignore[method-assign]
        (action, arguments)
    )
    screen._objective = "objective.json"
    screen._demand = "demand.json"

    screen._plan_availability("availability.json")

    assert seen == [
        (
            "plan",
            [
                "--objective-file",
                "objective.json",
                "--demand-file",
                "demand.json",
                "--availability-file",
                "availability.json",
            ],
        )
    ]


def test_resource_delete_uses_shared_reference_safety(
    tmp_path: Path,
) -> None:
    owner = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="refs")
    record = ModelProviderConnectionApplication(owner).configure(
        "local", provider="ollama", models=("qwen3:8b",)
    )
    launch = owner.paths.config / "launches" / "paper.toml"
    launch.parent.mkdir(parents=True, exist_ok=True)
    launch.write_text(
        '[launch]\nid = "paper"\nmode = "paper"\n\n'
        '[agent.model]\nconnection = "local"\nmodel = "qwen3:8b"\n',
        encoding="utf-8",
    )

    async def run() -> None:
        state = WorkbenchState(owner=owner, workspace_arg=owner.paths.root)
        app = KairosWorkbenchApp(state)
        async with app.run_test(size=(100, 30)) as pilot:
            screen = ResourceDetailScreen("models", dict(record))
            app.push_screen(screen)
            await pilot.pause()
            with pytest.raises(ValueError, match="is referenced"):
                screen._execute("delete", None)

    asyncio.run(run())
    assert ModelProviderConnectionApplication(owner).show("local")


def test_resource_external_test_uses_confirmation_modal() -> None:
    async def run() -> bool:
        app = KairosWorkbenchApp(_state())
        record = {"connection_id": "massive", "enabled": True}
        async with app.run_test(size=(100, 30)) as pilot:
            screen = ResourceDetailScreen("data", record)
            app.push_screen(screen)
            await pilot.pause()
            screen._confirm_test(None)
            await pilot.pause()
            return isinstance(app.screen, ConfirmDialog)

    assert asyncio.run(run())


def test_resource_external_test_respects_dry_run_even_with_yes() -> None:
    async def run() -> str:
        state = _state()
        state.dry_run = True
        state.yes = True
        app = KairosWorkbenchApp(state)
        record = {"connection_id": "massive", "enabled": True}
        async with app.run_test(size=(100, 30)) as pilot:
            screen = ResourceDetailScreen("data", record)
            app.push_screen(screen)
            await pilot.pause()
            screen._confirm_test(None)
            await pilot.pause()
            return str(screen.query_one("#resource-detail-result", RichLog).lines)

    assert "preview" in asyncio.run(run())


def test_yes_skips_confirmation_for_external_download_and_profile_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> tuple[list[str], list[tuple[str, str]], bool]:
        state = _state()
        state.yes = True
        app = KairosWorkbenchApp(state)
        downloads: list[str] = []
        mutations: list[tuple[str, str]] = []
        async with app.run_test(size=(100, 30)) as pilot:
            history = MarketHistoryScreen(_market())
            app.push_screen(history)
            await pilot.pause()
            monkeypatch.setattr(
                history, "_download", lambda: downloads.append(history.destination)
            )
            history._destination_selected("history/aapl.jsonl")
            await pilot.pause()
            app.pop_screen()
            profile = ProfileScreen()
            app.push_screen(profile)
            await pilot.pause()
            monkeypatch.setattr(
                profile,
                "_mutate",
                lambda action, name: mutations.append((action, name)),
            )
            profile._confirm_create("paper")
            await pilot.pause()
            return downloads, mutations, isinstance(app.screen, ConfirmDialog)

    downloads, mutations, confirmation_open = asyncio.run(run())

    assert downloads == ["history/aapl.jsonl"]
    assert mutations == [("create", "paper")]
    assert confirmation_open is False


def test_launch_setup_is_native_form_with_mode_context_and_cancel() -> None:
    async def run() -> tuple[bool, bool, bool]:
        app = KairosWorkbenchApp(_state())
        result: list[dict[str, object] | None] = []
        async with app.run_test(size=(100, 34)) as pilot:
            app.push_screen(LaunchSetupScreen("new-launch"), result.append)
            await pilot.pause()
            mode = app.screen.query_one("#launch-mode", Select)
            initial_backtest = app.screen.query_one(
                "#backtest-fields", Vertical
            ).display
            mode.value = "live"
            await pilot.pause()
            live_visible = app.screen.query_one("#live-fields", Vertical).display
            backtest_hidden = not app.screen.query_one(
                "#backtest-fields", Vertical
            ).display
            await pilot.press("escape")
            await pilot.pause()
            return initial_backtest, live_visible and backtest_hidden, result == [None]

    initial_backtest, switched_to_live, cancelled = asyncio.run(run())

    assert initial_backtest is False
    assert switched_to_live
    assert cancelled


def test_launch_setup_preserves_account_scope_and_agent_review_fields(
    tmp_path: Path,
) -> None:
    owner = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="launch")
    source = owner.paths.config / "launches" / "source.toml"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        '[launch]\nid = "source"\nmode = "paper"\nstrategy = "builtin:test"\n\n'
        '[accounts.main]\nref = "acct"\nenabled = true\ntrade = true\nsegments = ["spot"]\n\n'
        '[execution]\nenabled = false\n\n'
        '[agent]\nenabled = true\nrequired = false\nruntime = "model-agent"\n\n'
        '[agent.profile]\nversion = "2"\ngoal = "bounded review"\n'
        'rubric = ["bounded"]\ninvalidation_rules = ["stale"]\n'
        'reason_codes = ["ok"]\nrisk_flags = ["high"]\n\n'
        '[agent.model]\nconnection = "local"\nmodel = "qwen"\n\n'
        '[agent.capabilities.intent_review]\ninitial_mode = "shadow"\n'
        'strategy_selectable_modes = ["shadow", "gate"]\n'
        'operations = ["target_position"]\nrequired_contexts = ["account"]\n',
        encoding="utf-8",
    )

    async def run() -> dict[str, Any]:
        state = WorkbenchState(owner=owner, workspace_arg=owner.paths.root)
        app = KairosWorkbenchApp(state)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = LaunchSetupScreen("source", source)
            app.push_screen(screen)
            await pilot.pause()
            screen._save(publish=False)
        path = owner.paths.config / "launches" / ".drafts" / "source.toml"
        return tomllib.loads(path.read_text(encoding="utf-8"))

    values = asyncio.run(run())

    assert values["accounts"]["account_1"]["segments"] == ["spot"]
    assert values["accounts"]["account_1"]["trade"] is True
    assert values["agent"]["profile"]["version"] == "2"
    assert values["agent"]["capabilities"]["intent_review"][
        "strategy_selectable_modes"
    ] == ["shadow", "gate"]


def test_launch_publish_uses_shared_confirmation_modal() -> None:
    async def run() -> bool:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 34)) as pilot:
            app.push_screen(LaunchSetupScreen("new-launch"))
            await pilot.pause()
            app.screen.query_one("#publish", Button).press()
            await pilot.pause()
            return isinstance(app.screen, ConfirmDialog)

    assert asyncio.run(run())


def test_execution_submit_validation_stays_in_shared_form() -> None:
    async def run() -> tuple[bool, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 34)) as pilot:
            app.push_screen(ExecutionSubmitScreen("demo", "instance-1", "paper"))
            await pilot.pause()
            app.screen.query_one("#submit", Button).press()
            await pilot.pause()
            return (
                isinstance(app.screen, ExecutionSubmitScreen),
                str(app.screen.query_one("#execution-order-error", Label).render()),
            )

    same_screen, error = asyncio.run(run())

    assert same_screen
    assert error == "请填写所有必填项"
