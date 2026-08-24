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
from kairospy.surface.workbench.screens.guided.strategy import LaunchWizardState
from kairospy.surface.console.models import ObserveSnapshot
from kairospy.surface.workbench.widgets import ActionList, WorkbenchCommandInput
from textual.app import App
from textual.containers import Vertical
from textual.widgets import Button, DataTable, Input, Label, RichLog, Select, Static


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
    async def run() -> tuple[bool, str, str, str, int, bool, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            command_input = app.screen.query_one(
                "#command-input", WorkbenchCommandInput
            )
            output = _log_text(app.screen.query_one("#command-output", RichLog))
            context = str(app.screen.query_one("#command-context", Static).render())
            actions = app.screen.query_one("#guided-actions", ActionList)
            return (
                isinstance(app.screen, CommandLineScreen),
                app.screen.sub_title or "",
                output,
                context,
                actions.option_count,
                actions.can_focus,
                command_input.has_focus,
            )

    (
        is_command_screen,
        subtitle,
        output,
        context,
        option_count,
        actions_can_focus,
        input_focused,
    ) = asyncio.run(run())

    assert is_command_screen
    assert subtitle == "命令"
    assert "Kairos Workbench" in output
    assert "trader" in output
    assert "输入 1–6 选择" in output
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

    assert "Kairos Workbench" in clipboard
    assert "api_key=<redacted>" in clipboard
    assert "should-not-leak" not in clipboard
    assert any(event["event"] == "page_copied" for event in events)


def test_worker_busy_state_blocks_reentry_and_ctrl_c_restores_input() -> None:
    release = threading.Event()

    async def run() -> tuple[str, bool, str, bool, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen._run("slow", lambda: release.wait(2))
            await pilot.pause(0.05)
            busy_mode = screen.session.prompt_mode.value
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
                screen.session.prompt_mode.value,
                command_input.has_focus and not command_input.disabled,
                busy_output,
            )

    busy_mode, disabled, restored_mode, usable, output = asyncio.run(run())
    assert busy_mode == "busy"
    assert disabled
    assert restored_mode == "navigation"
    assert usable
    assert "当前任务仍在运行" in output


def test_idle_ctrl_c_requests_exit_confirmation_in_same_input() -> None:
    async def run() -> tuple[int | None, str, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            await pilot.press("ctrl+c")
            await pilot.pause()
            status = str(screen.query_one("#command-status", Static).render())
            output = _log_text(screen.query_one("#command-output", RichLog))
            focused = screen.query_one(
                "#command-input", WorkbenchCommandInput
            ).has_focus
            screen.submit("/confirm")
            await pilot.pause()
            return app.return_value, status, output, focused

    return_value, status, output, focused = asyncio.run(run())
    assert return_value == 0
    assert status == "等待确认"
    assert "当前没有运行中的任务，是否退出 Kairos Workbench？" in output
    assert "再次按 Ctrl+C 可强制退出" in output
    assert "/confirm" in output
    assert focused


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
    assert "当前正在等待确认" in output
    assert "已取消：当前没有运行中的任务" in output


def test_slash_exit_closes_workbench_even_while_argument_is_pending() -> None:
    async def run() -> int | None:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)):
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.enter_section("market")
            screen.submit("1")
            assert screen.session.pending_action == "market"
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
        "kairospy.surface.workbench.screens.guided.kairos_command.NativeCliApplication.run",
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
            return _log_text(app.screen.query_one("#command-output", RichLog))

    assert "Kairos Workbench" in asyncio.run(run())


def test_command_screen_renders_when_no_color_is_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")

    async def run() -> str:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(60, 20)) as pilot:
            await pilot.pause()
            return _log_text(app.screen.query_one("#command-output", RichLog))

    assert "Kairos Workbench" in asyncio.run(run())


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
            return (
                _log_text(app.screen.query_one("#command-output", RichLog)),
                command_input.has_focus,
            )

    output, input_focused = asyncio.run(run())
    assert "/market [代码]" in output
    assert input_focused


def test_market_command_guides_missing_argument_and_escape_cancels() -> None:
    async def run() -> tuple[str, str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("slash", "m", "a", "r", "k", "e", "t", "enter")
            await pilot.pause()
            command_input = app.screen.query_one(
                "#command-input", WorkbenchCommandInput
            )
            guided_placeholder = command_input.placeholder or ""
            guided_status = str(
                app.screen.query_one("#command-status", Static).render()
            )
            await pilot.press("escape")
            await pilot.pause()
            ready_status = str(app.screen.query_one("#command-status", Static).render())
            return guided_placeholder, guided_status, ready_status

    placeholder, guided_status, ready_status = asyncio.run(run())

    assert placeholder == "请输入市场代码或名称"
    assert guided_status == "等待输入 · market"
    assert ready_status == "首页 · 等待输入"


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


def test_worker_error_is_rendered_and_input_remains_usable() -> None:
    def fail(_: str) -> tuple[Market, ...]:
        raise RuntimeError("reference database unavailable")

    async def run() -> tuple[str, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen._find_markets = fail  # type: ignore[method-assign]
            screen.submit("/market AAPL")
            await pilot.pause(0.1)
            command_input = screen.query_one("#command-input", WorkbenchCommandInput)
            return (
                _log_text(screen.query_one("#command-output", RichLog)),
                str(screen.query_one("#command-status", Static).render()),
                command_input.has_focus,
            )

    output, status, input_focused = asyncio.run(run())

    assert "reference database unavailable" in output
    assert status == "失败 · 可继续输入"
    assert input_focused


def test_command_layout_runs_at_supported_terminal_sizes() -> None:
    async def run(size: tuple[int, int]) -> str:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            return _log_text(app.screen.query_one("#command-output", RichLog))

    for size in ((60, 20), (80, 24), (120, 30), (160, 40)):
        assert "Kairos Workbench" in asyncio.run(run(size))


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


def test_help_action_writes_into_command_output_without_opening_a_modal() -> None:
    async def run() -> tuple[bool, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app.screen
            app.action_help()
            await pilot.pause()
            content = _log_text(app.screen.query_one("#command-output", RichLog))
            return app.screen is screen, content

    stayed_inline, content = asyncio.run(run())

    assert stayed_inline
    assert "/help" in content
    assert "/market [代码]" in content
