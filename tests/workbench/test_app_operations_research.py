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
from kairospy.surface.workbench.screens.flows import operations, research
from kairospy.surface.workbench.screens.flows.operations.views import (
    ServiceDisplayState,
    diagnostics_renderable,
    service_actions,
    service_status_view,
    service_summary,
)
from kairospy.surface.workbench.screens.flows.launch.wizard import LaunchWizardState
from kairospy.system.apps.observe.application import ObserveSnapshot
from kairospy.surface.workbench.widgets import (
    ActionList,
    ConfirmInteraction,
    WorkbenchCommandInput,
    interaction_copy_text,
    renderable_plain_text,
)
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
    assert context == "首页 / 系统维护 / 工作区管理  ›"
    assert option_count == 4
    assert focused


def test_project_init_collects_each_field_in_the_shared_bottom_input() -> None:
    async def run() -> tuple[type[object], str, str, str, bool]:
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
    assert context == "首页 / 系统维护 / 工作区管理  ›"
    assert "demo-project" in output
    assert "项目操作结果" in output
    assert "preview" in output
    assert focused


def test_profile_create_uses_inline_confirmation_and_back_returns_to_config() -> None:
    async def run() -> tuple[str, str, ConfirmInteraction, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("6", "5", "7", "2", "paper"):
                screen.submit(value)
            await pilot.pause()
            status = str(screen.query_one("#command-status", Static).render())
            output = _log_text(screen.query_one("#command-output", RichLog))
            interaction = screen.session.interaction
            assert isinstance(interaction, ConfirmInteraction)
            screen.submit("/cancel")
            screen.submit("/back")
            return (
                status,
                output,
                interaction,
                str(screen.query_one("#command-context", Static).render()),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    status, output, interaction, context, focused = asyncio.run(run())
    assert status == "等待确认"
    assert output == ""
    assert "create Profile paper" in str(interaction.summary)
    assert context == "首页 / 系统维护 / 高级设置  ›"
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

    monkeypatch.setattr(operations, "execute_business", execute)

    async def run() -> tuple[type[object], str, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("6", "6", "1", "3", "policy.json", "request.json"):
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
        operations,
        "execute_business",
        lambda state, prompt: {"capability": getattr(prompt, "action")},
    )

    async def run() -> tuple[str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("6", "6", "3", "2"):
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
    assert after_back == "首页 / 系统维护 / 风控与集成工具  ›"


def test_operations_service_selection_actions_and_back_use_one_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        operations,
        "list_services",
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
            selected_actions = interaction_copy_text(screen.session.interaction)
            screen.submit("/back")
            await pilot.pause()
            services = str(screen.query_one("#command-context", Static).render())
            return (
                type(app.screen),
                selected,
                selected_actions,
                services,
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    screen_type, selected, selected_actions, services, focused = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert selected == "首页 / 系统维护 / market  ›"
    assert "停止" in selected_actions
    assert "重启" in selected_actions
    assert "reference" not in selected_actions
    assert services == "首页 / 系统维护 / 后台服务  ›"
    assert focused


def test_service_status_copy_and_actions_follow_lifecycle_state() -> None:
    stopped = service_status_view(
        {
            "component": "market",
            "status": "not_running",
            "control_reachable": False,
            "probe_error": "Connection refused",
        }
    )
    stale = service_status_view(
        {
            "component": "reference",
            "status": "not_running",
            "control_reachable": False,
            "control_socket_exists": True,
            "probe_error": "Connection refused",
        }
    )
    running = service_status_view({"component": "market", "status": "ready", "pid": 42})

    assert stopped.state is ServiceDisplayState.STOPPED
    assert stale.state is ServiceDisplayState.STALE
    assert running.state is ServiceDisplayState.RUNNING
    assert {item.id for item in service_actions(stopped)} == {
        "start",
        "logs",
        "follow",
        "diagnostics",
    }
    assert "stop" not in {item.id for item in service_actions(stopped)}
    assert {item.id for item in service_actions(stale)} >= {"repair", "repair-start"}
    assert {item.id for item in service_actions(running)} >= {"stop", "restart"}


def test_stopped_service_detail_keeps_one_action_list_at_60x20(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        operations,
        "list_services",
        lambda state: ({"component": "market", "status": "not_running"},),
    )

    async def run() -> tuple[type[object], int, int, int, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(60, 20)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("6", "3", "1"):
                screen.submit(value)
                await pilot.pause(0.1)
            return (
                type(app.screen),
                len(screen.query(ActionList)),
                screen.query_one("#guided-actions", ActionList).option_count,
                len(screen.query(WorkbenchCommandInput)),
                interaction_copy_text(screen.session.interaction),
            )

    screen_type, action_lists, options, inputs, interaction = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert action_lists == 1
    assert options == 4
    assert inputs == 1
    assert "启动" in interaction
    assert "停止 —" not in interaction


def test_service_summary_hides_technical_paths_until_diagnostics() -> None:
    view = service_status_view(
        {
            "component": "reference",
            "status": "stale",
            "control_socket": "/workspace/run/reference/control.sock",
            "health_file": "/workspace/run/reference/health.json",
            "process_lock": "/workspace/run/reference/process.lock",
            "probe_error": "Connection refused",
        }
    )

    summary = renderable_plain_text(service_summary(view))
    diagnostics = renderable_plain_text(diagnostics_renderable(view))
    assert "control.sock" not in summary
    assert "process.lock" not in summary
    assert "Connection refused" not in summary
    assert "control.sock" in diagnostics
    assert "process.lock" in diagnostics
    assert "Connection refused" in diagnostics


def test_operations_service_logs_flow_in_content_without_activity_pollution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refreshes: list[int] = []
    monkeypatch.setattr(
        operations,
        "list_services",
        lambda state: (
            {
                "component": "market",
                "status": "ready",
                "pid": 42,
                "log_file": "/workspace/logs/market/process.log",
                "logs_available": True,
            },
        ),
    )

    def execute_service(state: object, component: str, action: str) -> object:
        assert component == "market"
        assert action == "log-tail"
        refreshes.append(len(refreshes) + 1)
        return {
            "component": component,
            "lines": [f"market-log-{index}" for index in refreshes],
        }

    monkeypatch.setattr(operations, "execute_service", execute_service)

    async def run() -> tuple[str, str, int, int, str, int, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("6", "3", "1", "5"):
                screen.submit(value)
                await pilot.pause(0.1)
            await pilot.pause(1.1)
            output = screen.query_one("#command-output", RichLog)
            live_text = _log_text(output)
            activity_count = len(screen._output().activities)
            screen.submit("/p")
            await pilot.pause(1.1)
            buffer = screen.session.operations.live_buffer
            assert buffer is not None
            unseen = buffer.unseen_lines
            screen.submit("/back")
            await pilot.pause()
            return (
                live_text,
                screen._output().export_plain_text(),
                activity_count,
                unseen,
                str(screen.query_one("#command-context", Static).render()),
                len(refreshes),
                screen._operations_log_worker is None,
            )

    live, exported, activity_count, unseen, context, refresh_count, worker_closed = (
        asyncio.run(run())
    )
    assert "market-log" in live
    assert "market-log" not in exported
    assert activity_count == 1
    assert unseen > 0
    assert refresh_count >= 2
    assert worker_closed
    assert "已结束 market 日志跟随" in exported
    assert context == "首页 / 系统维护 / market  ›"


def test_operations_log_rotation_does_not_hide_repeated_first_line() -> None:
    async def run() -> tuple[int, tuple[str, ...]]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.session.operations.selected_service = "market"
            screen.session.operations.start_logs("market", started_at=0.0)
            screen.session.context = ("operations", "service-logs", "market")
            screen._render_operations_logs(
                {
                    "generation": "device:inode-one",
                    "size": 20,
                    "path": "/logs/market/process.log",
                    "lines": ["same-first-line"],
                }
            )
            screen._render_operations_logs(
                {
                    "generation": "device:inode-two",
                    "size": 20,
                    "path": "/logs/market/process.log",
                    "lines": ["same-first-line"],
                }
            )
            screen._render_operations_logs(
                {
                    "generation": "device:inode-two",
                    "size": 5,
                    "path": "/logs/market/process.log",
                    "lines": ["same-first-line"],
                }
            )
            await pilot.pause()
            buffer = screen.session.operations.live_buffer
            assert buffer is not None
            return screen.session.operations.received_lines, tuple(buffer.lines)

    received, lines = asyncio.run(run())
    assert received == 3
    assert lines == ("same-first-line", "same-first-line", "same-first-line")


def test_workspace_market_control_uses_single_input_and_inline_confirmation() -> None:
    async def run() -> tuple[type[object], str, str, ConfirmInteraction, bool]:
        state = _state()
        app = KairosWorkbenchApp(state)
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.enter_section("market")
            screen.submit("/c")
            screen.submit("/p")
            await pilot.pause()
            interaction = screen.session.interaction
            assert isinstance(interaction, ConfirmInteraction)
            return (
                type(app.screen),
                str(screen.query_one("#command-context", Static).render()),
                _log_text(screen.query_one("#command-output", RichLog)),
                interaction,
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    screen_type, context, output, interaction, focused = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert context == "首页 / 市场行情 / 运行中 Market  ›"
    assert output == ""
    assert interaction.title == "Workspace Market 操作确认"
    assert focused


def test_research_read_flow_uses_nested_single_input_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        research,
        "execute_research",
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
            assert screen.session.research.primary == "requirements.json"
            await pilot.press("slash", "b", "a", "c", "k", "enter")
            await pilot.pause()
            return (
                screen.session.research.action,
                screen.session.research.primary,
                screen.session.interaction.mode.value,
            )

    action, primary, mode = asyncio.run(run())
    assert action is None
    assert primary is None
    assert mode == "choice"
