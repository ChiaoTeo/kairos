from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import threading
import tomllib
from types import SimpleNamespace
from typing import Any

import pytest
from rich.text import Text
from textual.app import App
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Label, RichLog, Select, Static

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
from kairospy.surface.workbench.screens.flows import market
from kairospy.surface.workbench.screens.operation import OperationSpec
from kairospy.surface.workbench.screens.results import ResultKind, ResultRoute
from kairospy.surface.workbench.screens.flows.launch.wizard import LaunchWizardState
from kairospy.system.apps.observe.application import ObserveSnapshot
from kairospy.surface.workbench.widgets import (
    ActionList,
    ConfirmInteraction,
    Feature,
    InputInteraction,
    RunningInteraction,
    WorkbenchCommandInput,
    interaction_copy_text,
)
from app_support import (
    log_text as _log_text,
    market as _market,
    workbench_state as _state,
)


def test_workbench_leaves_mouse_dragging_to_the_terminal(monkeypatch) -> None:
    runs: list[dict[str, object]] = []
    monkeypatch.setattr(App, "run", lambda self, **kwargs: runs.append(kwargs))

    KairosWorkbenchApp(_state()).run()
    KairosWorkbenchApp(_state()).run(mouse=True)

    assert runs == [{"mouse": False}, {"mouse": True}]


def test_workbench_starts_as_one_guided_command_screen() -> None:
    async def run() -> tuple[bool, str, str, str, str, int, bool, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            command_input = app.screen.query_one(
                "#command-input", WorkbenchCommandInput
            )
            output = _log_text(app.screen.query_one("#command-output", RichLog))
            workspace_title = str(
                app.screen.query_one("#workspace-title", Static).render()
            )
            context = str(app.screen.query_one("#command-context", Static).render())
            actions = app.screen.query_one("#guided-actions", ActionList)
            return (
                isinstance(app.screen, CommandLineScreen),
                app.screen.sub_title or "",
                output,
                workspace_title,
                context,
                actions.option_count,
                actions.can_focus,
                command_input.has_focus,
            )

    (
        is_command_screen,
        subtitle,
        output,
        workspace_title,
        context,
        option_count,
        actions_can_focus,
        input_focused,
    ) = asyncio.run(run())

    assert is_command_screen
    assert subtitle == "命令"
    assert output == ""
    assert workspace_title == "KAIROS  /  trader"
    assert context == "首页  ›"
    assert option_count == 6
    assert not actions_can_focus
    assert input_focused


def test_copy_page_copies_complete_redacted_output_for_agent() -> None:
    async def run() -> tuple[str, tuple[dict[str, object], ...]]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            log = app.screen.query_one("#command-output", RichLog)
            log.write("api_key=should-not-leak")
            await pilot.press("ctrl+shift+c")
            return app._clipboard, app.transcript.events

    clipboard, events = asyncio.run(run())

    assert "Workspace: trader" in clipboard
    assert "## 当前交互" in clipboard
    assert "api_key=<redacted>" in clipboard
    assert "should-not-leak" not in clipboard
    assert any(event["event"] == "page_copied" for event in events)


def test_worker_busy_state_blocks_reentry_and_ctrl_c_restores_input() -> None:
    release = threading.Event()

    async def run() -> tuple[str, bool, RunningInteraction, str, bool, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen._start_operation(
                OperationSpec.create(
                    action_name="test.slow",
                    audit_summary="执行慢速测试任务",
                    route=ResultRoute(ResultKind.CONFIRMED),
                    operation=lambda: release.wait(2),
                    running_status="正在执行慢速测试任务",
                )
            )
            await pilot.pause(0.05)
            busy_mode = screen.session.interaction.mode.value
            running = screen.session.interaction
            assert isinstance(running, RunningInteraction)
            disabled = screen.query_one(
                "#command-input", WorkbenchCommandInput
            ).disabled
            screen.submit("/help")
            busy_output = _log_text(screen.query_one("#command-output", RichLog))
            await pilot.press("ctrl+c")
            await pilot.pause(0.1)
            release.set()
            await pilot.pause()
            command_input = screen.query_one("#command-input", WorkbenchCommandInput)
            return (
                busy_mode,
                disabled,
                running,
                screen.session.interaction.mode.value,
                command_input.has_focus and not command_input.disabled,
                busy_output,
            )

    busy_mode, disabled, running, restored_mode, usable, output = asyncio.run(run())
    assert busy_mode == "running"
    assert disabled
    assert running.cancellable
    assert restored_mode == "choice"
    assert usable
    assert "当前任务仍在运行" not in output


def test_idle_ctrl_c_requests_exit_confirmation_in_interaction_region() -> None:
    async def run() -> tuple[int | None, str, str, ConfirmInteraction, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            await pilot.press("ctrl+c")
            await pilot.pause()
            status = str(screen.query_one("#command-status", Static).render())
            output = _log_text(screen.query_one("#command-output", RichLog))
            interaction = screen.session.interaction
            assert isinstance(interaction, ConfirmInteraction)
            focused = screen.query_one(
                "#command-input", WorkbenchCommandInput
            ).has_focus
            screen.submit("/confirm")
            await pilot.pause()
            return app.return_value, status, output, interaction, focused

    return_value, status, output, interaction, focused = asyncio.run(run())
    assert return_value == 0
    assert status == "等待确认"
    assert output == ""
    assert interaction.summary == "当前没有运行中的任务，是否退出？"
    assert interaction.force_hint is not None
    assert "再次按 Ctrl+C 强制退出" in interaction.force_hint
    assert focused


def test_large_interaction_is_bounded_and_keeps_command_bar_visible() -> None:
    async def run() -> tuple[int, int, int, int, int, float, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.session.confirm(
                OperationSpec.create(
                    action_name="large operation",
                    audit_summary="large operation",
                    route=ResultRoute(ResultKind.CONFIRMED),
                    operation=lambda: None,
                    running_status="正在执行：large operation",
                ),
                title="需要确认",
                display_summary=Text(
                    "\n".join(f"preview line {index}" for index in range(30))
                ),
            )
            interaction = screen._interaction()
            interaction.present(screen.session.interaction)
            await pilot.pause()
            output = screen.query_one("#command-output", RichLog)
            command_bar = screen.query_one("#command-bar", Horizontal)
            await pilot.press("alt+pagedown")
            await pilot.pause()
            return (
                interaction.region.height,
                interaction.virtual_size.height,
                output.region.height,
                command_bar.region.bottom,
                screen.region.height,
                interaction.scroll_y,
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    (
        interaction_height,
        virtual_height,
        output_height,
        command_bottom,
        screen_height,
        scroll_y,
        input_focused,
    ) = asyncio.run(run())

    assert interaction_height <= screen_height * 0.4
    assert virtual_height > interaction_height
    assert output_height >= 5
    assert command_bottom <= screen_height
    assert scroll_y > 0
    assert input_focused


def test_output_paging_keeps_input_focus_and_ctrl_end_resumes_follow() -> None:
    async def run() -> tuple[float, int, bool, float, float, float, bool, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            output = screen._output()
            for index in range(30):
                output.write(f"line {index}")
            await pilot.pause()
            initial_y = output.scroll_y
            initial_end = output.max_scroll_y
            await pilot.press("pageup")
            await pilot.pause()
            browsed_y = output.scroll_y
            focused_while_browsing = screen._input().has_focus
            await pilot.press("alt+down")
            await pilot.pause()
            line_scrolled_y = output.scroll_y
            await pilot.press("ctrl+end")
            await pilot.pause()
            return (
                initial_y,
                initial_end,
                focused_while_browsing,
                browsed_y,
                line_scrolled_y,
                output.scroll_y,
                screen._input().has_focus,
                str(screen.query_one("#command-hints", Static).render()),
            )

    (
        initial_y,
        initial_end,
        browsing_focus,
        browsed_y,
        line_scrolled_y,
        resumed_y,
        resumed_focus,
        hints,
    ) = asyncio.run(run())

    assert initial_y == initial_end
    assert browsed_y < initial_end
    assert line_scrolled_y > browsed_y
    assert browsing_focus
    assert resumed_y == initial_end
    assert resumed_focus
    assert "Alt+↑↓ 滚动" in hints
    assert "PgUp/PgDn 翻页" in hints
    assert "Ctrl+End 最新" in hints


def test_second_idle_ctrl_c_forces_exit() -> None:
    async def run() -> int | None:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("ctrl+c")
            await pilot.pause()
            await pilot.press("ctrl+c")
            await pilot.pause()
        return app.return_value

    assert asyncio.run(run()) == 130


def test_input_between_ctrl_c_presses_breaks_force_exit_sequence() -> None:
    async def run() -> tuple[int | None, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            await pilot.press("ctrl+c")
            await pilot.pause()
            screen.submit("account overview paper-main")
            await pilot.press("ctrl+c")
            await pilot.pause()
            return (
                app.return_value,
                _log_text(screen.query_one("#command-output", RichLog)),
            )

    return_value, output = asyncio.run(run())
    assert return_value is None
    assert "当前正在等待确认" not in output
    assert "已取消" not in output


def test_slash_exit_closes_workbench_even_while_argument_is_pending() -> None:
    async def run() -> int | None:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)):
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.enter_section("market")
            screen.submit("1")
            assert isinstance(screen.session.interaction, InputInteraction)
            assert screen.session.interaction.action.feature is Feature.MARKET
            screen.submit("/exit")
        return app.return_value

    assert asyncio.run(run()) == 0


@pytest.mark.parametrize("command", ("exit", "quit", "q", "help", "back"))
def test_bare_words_are_treated_as_kairos_commands_not_workbench_commands(
    command: str,
) -> None:
    async def run() -> tuple[int | None, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit(command)
            await pilot.pause(0.1)
            return app.return_value, _log_text(
                screen.query_one("#command-output", RichLog)
            )

    return_value, output = asyncio.run(run())
    assert return_value is None
    assert f"kairos {command} 尚未接入" in output


def test_bare_native_command_uses_owner_cli_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def run_native(
        application: object, component: str, arguments: list[str]
    ) -> dict[str, object]:
        seen.update(component=component, arguments=arguments)
        return {"schema": "risk-v1"}

    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.commands.NativeCliApplication.run",
        run_native,
    )

    async def run() -> tuple[str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("risk schema")
            await pilot.pause(0.1)
            return (
                _log_text(screen.query_one("#command-output", RichLog)),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    output, focused = asyncio.run(run())
    assert seen == {"component": "risk", "arguments": ["standalone", "schema"]}
    assert "risk-v1" in output
    assert focused


def test_external_workbench_stylesheet_is_loaded_and_watchable() -> None:
    normal_app = KairosWorkbenchApp(_state())
    app = KairosWorkbenchApp(_state(), watch_css=True)

    assert [path.name for path in app.css_path] == ["workbench.tcss"]
    assert normal_app.css_monitor is None
    assert app.css_monitor is not None


@pytest.mark.parametrize("theme", ("textual-dark", "textual-light"))
def test_command_screen_renders_in_supported_terminal_themes(theme: str) -> None:
    async def run() -> str:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            app.theme = theme
            await pilot.pause()
            return str(app.screen.query_one("#workspace-title", Static).render())

    assert asyncio.run(run()) == "KAIROS  /  trader"


def test_command_screen_renders_when_no_color_is_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")

    async def run() -> str:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(60, 20)) as pilot:
            await pilot.pause()
            return str(app.screen.query_one("#workspace-title", Static).render())

    assert asyncio.run(run()) == "KAIROS  /  trader"


def test_workspace_identity_is_visible_in_shared_header_context() -> None:
    async def run() -> str:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            return app.screen.title or ""

    assert asyncio.run(run()) == "Kairos · trader"


def test_existing_setup_entry_starts_in_guided_product_context() -> None:
    async def run() -> tuple[type[object], str]:
        app = KairosWorkbenchApp(_state(), initial_section="resources")
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            return type(app.screen), str(
                app.screen.query_one("#command-context", Static).render()
            )

    screen_type, context = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert context == "首页 / 运行准备  ›"


def test_command_input_executes_help_and_keeps_focus() -> None:
    async def run() -> tuple[str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("slash", "h", "e", "l", "p", "enter")
            await pilot.pause()
            command_input = app.screen.query_one(
                "#command-input", WorkbenchCommandInput
            )
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            return interaction_copy_text(
                screen.session.interaction
            ), command_input.has_focus

    output, input_focused = asyncio.run(run())
    assert "/market [代码]" in output
    assert input_focused


def test_market_command_guides_missing_argument_and_escape_cancels() -> None:
    async def run() -> tuple[str, str, InputInteraction, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            await pilot.press("slash", "m", "a", "r", "k", "e", "t", "enter")
            await pilot.pause()
            command_input = screen.query_one("#command-input", WorkbenchCommandInput)
            guided_placeholder = command_input.placeholder or ""
            guided_status = str(screen.query_one("#command-status", Static).render())
            interaction = screen.session.interaction
            assert isinstance(interaction, InputInteraction)
            await pilot.press("escape")
            await pilot.pause()
            ready_status = str(screen.query_one("#command-status", Static).render())
            return guided_placeholder, guided_status, interaction, ready_status

    placeholder, guided_status, interaction, ready_status = asyncio.run(run())

    assert placeholder == "输入代码或名称"
    assert guided_status == "搜索市场 · 等待输入"
    assert interaction.prompt == "输入代码或名称"
    assert ready_status == "就绪"


def test_command_input_keeps_shell_style_history() -> None:
    async def run() -> tuple[str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("slash", "h", "e", "l", "p", "enter")
            await pilot.press("slash", "c", "l", "e", "a", "r", "enter")
            await pilot.press("up")
            command_input = app.screen.query_one(
                "#command-input", WorkbenchCommandInput
            )
            latest = command_input.value
            await pilot.press("up")
            previous = command_input.value
            return latest, previous

    assert asyncio.run(run()) == ("/clear", "/help")


def test_market_worker_error_keeps_search_prompt_usable_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_: str) -> tuple[Market, ...]:
        raise RuntimeError("reference database unavailable")

    monkeypatch.setattr(
        market,
        "load_records",
        lambda *args, **kwargs: fail(str(args[2])),
    )

    async def run() -> tuple[str, str, bool, str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("/market AAPL")
            await pilot.pause(0.1)
            command_input = screen.query_one("#command-input", WorkbenchCommandInput)
            return (
                _log_text(screen.query_one("#command-output", RichLog)),
                str(screen.query_one("#command-status", Static).render()),
                command_input.has_focus,
                screen.session.interaction.mode.value,
                command_input.placeholder or "",
            )

    output, status, input_focused, prompt_mode, placeholder = asyncio.run(run())

    assert "reference database unavailable" in output
    assert status == "搜索市场失败 · 请重试"
    assert input_focused
    assert prompt_mode == "input"
    assert placeholder == "输入代码或名称"


def test_command_layout_runs_at_supported_terminal_sizes() -> None:
    async def run(size: tuple[int, int]) -> str:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            return str(app.screen.query_one("#workspace-title", Static).render())

    for size in ((60, 20), (80, 24), (120, 30), (160, 40)):
        assert asyncio.run(run(size)) == "KAIROS  /  trader"


def test_text_input_consumes_global_shortcuts_as_text() -> None:
    async def run() -> tuple[str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            search = screen.query_one("#command-input", WorkbenchCommandInput)
            await pilot.press("q", "1", "?")
            await pilot.pause()
            return search.value, app.is_running

    value, running = asyncio.run(run())

    assert value == "q1?"
    assert running


def test_help_action_uses_interaction_region_without_opening_a_modal() -> None:
    async def run() -> tuple[bool, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app.screen
            app.action_help()
            await pilot.pause()
            assert isinstance(screen, CommandLineScreen)
            content = interaction_copy_text(screen.session.interaction)
            return app.screen is screen, content

    stayed_inline, content = asyncio.run(run())

    assert stayed_inline
    assert "/help" in content
    assert "/market [代码]" in content
