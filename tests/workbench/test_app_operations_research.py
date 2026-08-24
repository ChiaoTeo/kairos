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


def test_operations_nested_menus_never_replace_command_screen() -> None:
    async def run() -> tuple[type[object], str, int, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("6")
            screen.submit("1")
            await pilot.pause()
            return (
                type(app.screen),
                str(screen.query_one("#command-context", Static).render()),
                screen.query_one("#guided-actions", ActionList).option_count,
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    screen_type, context, option_count, focused = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert context == "首页 / 系统维护 / 项目工作区  ›"
    assert option_count == 4
    assert focused


def test_project_init_collects_each_field_in_the_shared_bottom_input() -> None:
    async def run() -> tuple[type[object], str, str, bool]:
        state = _state()
        state.dry_run = True
        app = KairosWorkbenchApp(state)
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("6", "1", "2", "demo-project", "", "none"):
                screen.submit(value)
                await pilot.pause(0.03)
            await pilot.pause(0.1)
            return (
                type(app.screen),
                str(screen.query_one("#command-context", Static).render()),
                _log_text(screen.query_one("#command-output", RichLog)),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    screen_type, context, output, focused = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert context == "首页 / 系统维护 / 项目工作区  ›"
    assert "demo-project" in output
    assert "项目操作结果" in output
    assert "preview" in output
    assert focused


def test_profile_create_uses_inline_confirmation_and_back_returns_to_config() -> None:
    async def run() -> tuple[str, str, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("6", "6", "7", "2", "paper"):
                screen.submit(value)
            await pilot.pause()
            status = str(screen.query_one("#command-status", Static).render())
            output = _log_text(screen.query_one("#command-output", RichLog))
            screen.submit("/cancel")
            screen.submit("/back")
            return (
                status,
                output,
                str(screen.query_one("#command-context", Static).render()),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    status, output, context, focused = asyncio.run(run())
    assert status == "等待确认"
    assert "create Profile paper" in output
    assert context == "首页 / 系统维护 / 高级配置  ›"
    assert focused


def test_risk_preview_collects_legacy_arguments_in_one_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, str]]] = []

    def execute(state: object, prompt: object) -> dict[str, str]:
        calls.append(
            (
                getattr(prompt, "tool"),
                getattr(prompt, "action"),
                dict(getattr(prompt, "values")),
            )
        )
        return {"decision": "allow"}

    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.command_line.execute_business", execute
    )

    async def run() -> tuple[type[object], str, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("6", "9", "1", "3", "policy.json", "request.json"):
                screen.submit(value)
            await pilot.pause(0.1)
            return (
                type(app.screen),
                str(screen.query_one("#command-context", Static).render()),
                _log_text(screen.query_one("#command-output", RichLog)),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    screen_type, context, output, focused = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert calls == [
        (
            "risk",
            "preview",
            {"policy": "policy.json", "request": "request.json"},
        )
    ]
    assert context == "首页 / 系统维护 / Risk  ›"
    assert "allow" in output
    assert focused


def test_integration_capability_and_nested_back_use_one_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.command_line.execute_business",
        lambda state, prompt: {"capability": getattr(prompt, "action")},
    )

    async def run() -> tuple[str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("6", "9", "3", "2"):
                screen.submit(value)
            await pilot.pause(0.1)
            result_context = str(screen.query_one("#command-context", Static).render())
            screen.submit("/back")
            return (
                result_context,
                str(screen.query_one("#command-context", Static).render()),
            )

    result_context, after_back = asyncio.run(run())
    assert result_context == "首页 / 系统维护 / Provider 集成  ›"
    assert after_back == "首页 / 系统维护 / 业务工具  ›"


def test_operations_service_selection_actions_and_back_use_one_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.command_line.list_operations_services",
        lambda state: ({"component": "market", "status": "ready", "pid": 42},),
    )

    async def run() -> tuple[type[object], str, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("6", "3"):
                screen.submit(value)
                await pilot.pause(0.1)
            assert screen.session.context == ("operations", "services")
            screen.submit("1")
            await pilot.pause()
            selected = str(screen.query_one("#command-context", Static).render())
            screen.submit("/back")
            await pilot.pause()
            services = str(screen.query_one("#command-context", Static).render())
            return (
                type(app.screen),
                selected,
                services,
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    screen_type, selected, services, focused = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert selected == "首页 / 系统维护 / 服务操作  ›"
    assert services == "首页 / 系统维护 / 系统服务  ›"
    assert focused


def test_workspace_market_control_uses_single_input_and_inline_confirmation() -> None:
    async def run() -> tuple[type[object], str, str, bool]:
        state = _state()
        app = KairosWorkbenchApp(state)
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.enter_section("market")
            screen.submit("/c")
            screen.submit("/p")
            await pilot.pause()
            return (
                type(app.screen),
                str(screen.query_one("#command-context", Static).render()),
                _log_text(screen.query_one("#command-output", RichLog)),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    screen_type, context, output, focused = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert context == "首页 / 市场行情 / 运行中 Market  ›"
    assert "Workspace Market pause" in output
    assert "/confirm" in output
    assert focused


def test_research_read_flow_uses_nested_single_input_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.command_line.execute_research",
        lambda state, action, value=None, extra=None: {
            "action": action,
            "value": value,
        },
    )

    async def run() -> tuple[type[object], str, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("5", "1", "2", "dataset-demo"):
                screen.submit(value)
                await pilot.pause(0.05)
            return (
                type(app.screen),
                str(screen.query_one("#command-context", Static).render()),
                _log_text(screen.query_one("#command-output", RichLog)),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    screen_type, context, output, focused = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert context == "首页 / 数据研究 / 数据准备  ›"
    assert "dataset-demo" in output
    assert focused


def test_research_multistep_cancel_clears_staged_values() -> None:
    async def run() -> tuple[str | None, str | None, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("5", "1", "4", "requirements.json"):
                screen.submit(value)
            assert screen.session.research_primary == "requirements.json"
            await pilot.press("slash", "b", "a", "c", "k", "enter")
            await pilot.pause()
            return (
                screen.session.research_action,
                screen.session.research_primary,
                screen.session.prompt_mode.value,
            )

    action, primary, mode = asyncio.run(run())
    assert action is None
    assert primary is None
    assert mode == "navigation"
