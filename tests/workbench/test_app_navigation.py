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
from kairospy.surface.workbench.screens.command_line import CommandLineScreen
from kairospy.surface.workbench.screens.flows.launch.wizard import LaunchWizardState
from kairospy.surface.workbench.screens.selection import SelectionRecord
from kairospy.system.apps.observe.application import ObserveSnapshot
from kairospy.surface.workbench.widgets import (
    ActionList,
    Feature,
    InputInteraction,
    WorkbenchCommandInput,
    interaction_copy_text,
)
from textual.app import App
from textual.containers import Vertical
from textual.widgets import Button, DataTable, Input, Label, RichLog, Select, Static


from app_support import (
    log_text as _log_text,
    market as _market,
    workbench_state as _state,
)


@pytest.mark.parametrize(
    ("shortcut", "context"),
    (
        ("1", "首页 / 市场行情  ›"),
        ("2", "首页 / 市场标的  ›"),
        ("3", "首页 / 策略管理  ›"),
        ("4", "首页 / 运行准备  ›"),
        ("5", "首页 / 数据研究  ›"),
        ("6", "首页 / 运行中心 / 运行概览  ›"),
        ("7", "项目管理  ›"),
    ),
)
def test_home_number_enters_product_context_without_replacing_input(
    shortcut: str, context: str
) -> None:
    async def run() -> tuple[type[object], str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press(shortcut, "enter")
            await pilot.pause()
            return (
                type(app.screen),
                str(app.screen.query_one("#command-context", Static).render()),
                app.screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    screen_type, actual_context, input_focused = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert actual_context == context
    assert input_focused


def test_missing_project_enters_project_start_before_business_home() -> None:
    async def run() -> tuple[str, str, int, tuple[str, ...], str]:
        state = WorkbenchState(
            owner=None,
            workspace_arg=None,
            load_error="尚未打开项目",
        )
        app = KairosWorkbenchApp(state)
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            initial_context = str(screen.query_one("#command-context", Static).render())
            initial_actions = interaction_copy_text(screen.session.interaction)
            initial_count = screen.query_one("#guided-actions", ActionList).option_count
            screen.submit("/home")
            screen.enter_section("market")
            await pilot.pause()
            return (
                initial_context,
                initial_actions,
                initial_count,
                screen.session.context,
                str(screen.query_one("#command-status", Static).render()),
            )

    context, actions, count, guarded_context, status = asyncio.run(run())
    assert context == "项目管理  ›"
    assert count == 2
    assert "打开项目" in actions
    assert "创建项目" in actions
    assert "查看市场行情" not in actions
    assert guarded_context == ("project",)
    assert status == "请先打开或创建项目"


def test_project_home_entry_and_global_shortcut_share_one_context() -> None:
    async def run(command: str) -> tuple[tuple[str, ...], str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit(command)
            await pilot.pause()
            return screen.session.context, interaction_copy_text(
                screen.session.interaction
            )

    numbered = asyncio.run(run("7"))
    shortcut = asyncio.run(run("p"))
    assert numbered[0] == shortcut[0] == ("project",)
    assert numbered[1] == shortcut[1]
    assert "项目概览" in numbered[1]
    assert "切换项目" in numbered[1]


def test_workspace_header_remains_project_identity_during_navigation() -> None:
    async def run(command: str) -> tuple[str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit(command)
            await pilot.pause()
            return (
                str(screen.query_one("#workspace-title", Static).render()),
                str(screen.query_one("#command-context", Static).render()),
            )

    operations_header, operations_context = asyncio.run(run("6"))
    project_header, project_context = asyncio.run(run("p"))

    assert operations_header == project_header == "KAIROS  /  trader"
    assert operations_context == "首页 / 运行中心 / 运行概览  ›"
    assert project_context == "项目管理  ›"


def test_switch_project_reloads_global_context_and_clears_old_selections(
    tmp_path: Path,
) -> None:
    first = WorkspaceApplication().init_project(
        tmp_path / "first", workspace_id="first"
    )
    second = WorkspaceApplication().init_project(
        tmp_path / "second", workspace_id="second"
    )

    async def run() -> tuple[str, tuple[str, ...], object, str]:
        state = WorkbenchState(
            owner=first,
            workspace_arg=first.paths.root,
            yes=True,
        )
        state.refresh_snapshot()
        app = KairosWorkbenchApp(state)
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.session.market.selected = _market()
            for value in ("7", "3", str(second.paths.project_root)):
                screen.submit(value)
                await pilot.pause(0.1)
            await pilot.pause(0.2)
            return (
                state.workspace_id,
                screen.session.context,
                screen.session.market.selected,
                str(screen.query_one("#workspace-title", Static).render()),
            )

    workspace_id, context, selected_market, header = asyncio.run(run())
    assert workspace_id == "second"
    assert context == ()
    assert selected_market is None
    assert "second" in header
    assert "first" not in header


def test_create_project_unlocks_the_project_home(
    tmp_path: Path,
) -> None:
    async def run() -> tuple[str, tuple[str, ...], int, str]:
        state = WorkbenchState(owner=None, workspace_arg=None, yes=True)
        app = KairosWorkbenchApp(state)
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("2", str(tmp_path / "created"), "demo", "none"):
                screen.submit(value)
                await pilot.pause(0.1)
            await pilot.pause(0.2)
            return (
                state.workspace_id,
                screen.session.context,
                screen.query_one("#guided-actions", ActionList).option_count,
                interaction_copy_text(screen.session.interaction),
            )

    workspace_id, context, count, actions = asyncio.run(run())
    assert workspace_id == "demo"
    assert context == ()
    assert count == 7
    assert "查看市场行情" in actions
    assert "运行中心" in actions
    assert "项目管理" in actions


def test_home_navigation_does_not_append_to_content_stream() -> None:
    async def run() -> tuple[str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            output = screen.query_one("#command-output", RichLog)
            before = _log_text(output)
            await pilot.press("1", "enter")
            await pilot.pause()
            return before, _log_text(output)

    before, after = asyncio.run(run())
    assert after == before
    assert "kairos › 1" not in after


def test_market_menu_keeps_advanced_operations_out_of_primary_choices() -> None:
    async def run() -> tuple[int, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("1", "enter")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            actions = screen.query_one("#guided-actions", ActionList)
            screen.submit("/help")
            return actions.option_count, interaction_copy_text(
                screen.session.interaction
            )

    option_count, output = asyncio.run(run())
    assert option_count == 3
    assert "/r" in output
    assert "/c" in output
    assert "/d" in output
    assert "/a" in output


def test_submenu_back_and_home_navigation_stay_out_of_content_stream() -> None:
    async def run() -> tuple[str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)):
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            output = screen.query_one("#command-output", RichLog)
            before = _log_text(output)
            screen.submit("5")
            screen.submit("1")
            screen.submit("/back")
            screen.submit("/home")
            return before, _log_text(output)

    before, after = asyncio.run(run())
    assert after == before


def test_slash_back_returns_from_result_to_section_then_home() -> None:
    async def run() -> tuple[str, str, type[object], bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.enter_section("reference")
            screen.session.context = ("reference", "markets")
            market = _market()
            screen.session.visible_records = (
                SelectionRecord(str(market.id), "AAPL", "NASDAQ · stock", market),
            )
            screen._show_context()

            await pilot.press("slash", "b", "a", "c", "k", "enter")
            await pilot.pause()
            section_context = str(screen.query_one("#command-context", Static).render())

            await pilot.press("slash", "b", "a", "c", "k", "enter")
            await pilot.pause()
            home_context = str(screen.query_one("#command-context", Static).render())
            return (
                section_context,
                home_context,
                type(app.screen),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    section, home, screen_type, focused = asyncio.run(run())
    assert section == "首页 / 市场标的  ›"
    assert home == "首页  ›"
    assert screen_type is CommandLineScreen
    assert focused


def test_slash_back_cancels_pending_argument_before_leaving_section() -> None:
    async def run() -> tuple[str, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.enter_section("market")
            screen.submit("1")
            assert isinstance(screen.session.interaction, InputInteraction)
            assert screen.session.interaction.action.feature is Feature.MARKET

            await pilot.press("slash", "b", "a", "c", "k", "enter")
            await pilot.pause()
            return (
                str(screen.query_one("#command-context", Static).render()),
                isinstance(screen.session.interaction, InputInteraction),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    context, pending, focused = asyncio.run(run())
    assert context == "首页 / 市场行情  ›"
    assert not pending
    assert focused


def test_ctrl_c_cancels_pending_argument_without_exiting_workbench() -> None:
    async def run() -> tuple[int | None, str, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.enter_section("market")
            screen.submit("1")
            await pilot.press("ctrl+c")
            await pilot.pause()
            return (
                app.return_value,
                isinstance(screen.session.interaction, InputInteraction),
                str(screen.query_one("#command-context", Static).render()),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    return_value, pending, context, focused = asyncio.run(run())
    assert return_value is None
    assert not pending
    assert context == "首页 / 市场行情  ›"
    assert focused


def test_ctrl_c_rejects_pending_confirmation_without_running_action() -> None:
    called: list[bool] = []

    async def run() -> tuple[int | None, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.request_confirmation("危险操作", lambda: called.append(True))
            await pilot.press("ctrl+c")
            await pilot.pause()
            return (
                app.return_value,
                _log_text(screen.query_one("#command-output", RichLog)),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    return_value, output, focused = asyncio.run(run())
    assert return_value is None
    assert called == []
    assert output == ""
    assert focused


def test_short_n_command_rejects_pending_confirmation() -> None:
    called: list[bool] = []

    async def run() -> tuple[int | None, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.request_confirmation("危险操作", lambda: called.append(True))
            screen.submit("/n")
            await pilot.pause()
            return app.return_value, screen.session.interaction.mode.value

    return_value, mode = asyncio.run(run())
    assert return_value is None
    assert called == []
    assert mode == "choice"


def test_all_home_products_enter_the_shared_command_screen() -> None:
    async def run(section: str) -> tuple[type[object], tuple[str, ...]]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            app.open_section(section)
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            return type(screen), screen.session.context

    for section in (
        "market",
        "reference",
        "strategy",
        "resources",
        "research",
        "operations",
    ):
        screen_type, context = asyncio.run(run(section))
        assert screen_type is CommandLineScreen
        assert context == (
            ("operations", "overview") if section == "operations" else (section,)
        )


@pytest.mark.parametrize("size", ((60, 20), (80, 24), (120, 30), (160, 40)))
@pytest.mark.parametrize(
    "section",
    ("market", "reference", "strategy", "resources", "research", "operations"),
)
def test_primary_contexts_mount_at_supported_terminal_sizes(
    size: tuple[int, int], section: str
) -> None:
    async def run() -> tuple[type[object], tuple[str, ...]]:
        app = KairosWorkbenchApp(_state(), observe_refresh_seconds=3600)
        async with app.run_test(size=size) as pilot:
            app.open_section(section)
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            return type(screen), screen.session.context

    screen_type, context = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert context == (
        ("operations", "overview") if section == "operations" else (section,)
    )
