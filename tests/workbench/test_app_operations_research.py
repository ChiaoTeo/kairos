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
from kairospy.surface.workbench.screens.activity import ActivityOutcome
from kairospy.surface.workbench.screens.effects import (
    AppendActivity,
    RunOperation,
    SetInteraction,
    SetStatus,
)
from kairospy.surface.workbench.screens.flows import operations, research
from kairospy.surface.workbench.screens.flows.operations.views import (
    ServiceDisplayState,
    diagnostics_renderable,
    project_result_renderable,
    service_actions,
    service_status_line,
    service_status_view,
    service_summary,
)
from kairospy.surface.workbench.screens.flows.launch.wizard import LaunchWizardState
from kairospy.surface.workbench.screens.operation import OperationSpec
from kairospy.surface.workbench.screens.results import ResultKind, ResultRoute
from kairospy.surface.workbench.screens.session import GuidedSession
from kairospy.system.apps.observe.application import ObserveSnapshot
from kairospy.surface.workbench.widgets import (
    ActionList,
    ChoiceInteraction,
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


def test_operations_center_opens_current_runtime_inventory_directly() -> None:
    async def run() -> tuple[type[object], str, str, int, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("6")
            await pilot.pause()
            return (
                type(app.screen),
                str(screen.query_one("#command-context", Static).render()),
                interaction_copy_text(screen.session.interaction),
                screen.query_one("#guided-actions", ActionList).option_count,
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    screen_type, context, copy, option_count, focused = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert context == "trader / 运行中心 / 运行概览  ›"
    assert option_count == 3
    assert "项目共享服务" in copy
    assert "活动运行实例" in copy
    assert "支撑进程" in copy
    assert "选择具体对象查看状态" not in copy
    assert "运行状态" not in copy
    assert "高级设置" not in copy
    assert focused


def test_stale_service_uses_action_only_interaction() -> None:
    async def run() -> tuple[str, int, bool, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(80, 20)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            view = service_status_view(
                {
                    "component": "reference",
                    "status": "stale",
                    "operating_mode": "stopped",
                    "logs_available": True,
                    "dependents": (),
                }
            )
            screen.session.operations.selected_service = "reference"
            screen.session.operations.selected_service_status = view
            screen.session.context = ("operations", "service", "reference")
            screen.session.choose(
                service_actions(view),
                title="trader / 运行中心 / 标的服务",
            )
            screen._interaction().present(screen.session.interaction)
            await pilot.pause()
            actions = screen.query_one("#guided-actions", ActionList)
            return (
                interaction_copy_text(screen.session.interaction),
                actions.option_count,
                screen.query_one("#interaction-content", Static).display,
                service_status_line(view),
            )

    copy, option_count, content_displayed, status = asyncio.run(run())
    assert "清理并启动" in copy
    assert "仅清理失效资源" in copy
    assert option_count == 4
    assert not content_displayed
    assert status == "标的服务 · 资源残留 · 无活动运行实例"


def test_service_start_failure_moves_detail_to_activity_and_marks_start_failed() -> None:
    session = GuidedSession(
        root_label="trader",
        context=("operations", "service", "reference"),
    )
    session.operations.selected_service = "reference"
    session.operations.selected_service_status = service_status_view(
        {
            "component": "reference",
            "status": "stale",
            "control_socket_exists": True,
            "logs_available": True,
        }
    )
    spec = OperationSpec.create(
        action_name="operations.service.repair-start",
        audit_summary="清理并启动标的服务",
        route=ResultRoute(ResultKind.OPERATIONS, "service-status"),
        operation=lambda: None,
        running_status="正在清理并启动标的服务…",
    )

    effects = operations.handle_failure(
        _state(), session, spec, "invalid reference configuration: unknown field products"
    )

    assert effects is not None
    activity = next(effect for effect in effects if isinstance(effect, AppendActivity))
    interaction = next(
        effect for effect in effects if isinstance(effect, SetInteraction)
    )
    status = next(effect for effect in effects if isinstance(effect, SetStatus))
    assert activity.activity.outcome is ActivityOutcome.FAILURE
    assert activity.activity.copy_text is not None
    assert "unknown field products" in activity.activity.copy_text
    assert isinstance(interaction.interaction, ChoiceInteraction)
    assert interaction.interaction.summary is None
    assert status.message == "操作失败 · 请选择恢复动作"
    assert session.operations.selected_service_status is not None
    assert (
        session.operations.selected_service_status.state
        is ServiceDisplayState.START_FAILED
    )
    assert (
        session.operations.selected_service_status.recommendation
        == "修正 Workspace 的 Reference 配置后重新启动服务。"
    )
    assert {item.id for item in interaction.interaction.actions} >= {
        "start",
        "logs",
        "diagnostics",
    }
    assert "repair-start" not in {
        item.id for item in interaction.interaction.actions
    }


def test_project_init_collects_each_field_in_the_shared_bottom_input() -> None:
    async def run() -> tuple[type[object], str, str, str, bool]:
        state = _state()
        state.dry_run = True
        app = KairosWorkbenchApp(state)
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("7", "4", "demo-project", "", "none"):
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
    assert context == "trader  ›"
    assert "demo-project" in output
    assert "项目操作预览" in output
    assert "尚未写入任何内容" in output
    assert focused


def test_project_status_uses_a_project_summary_instead_of_mapping_syntax() -> None:
    text = renderable_plain_text(
        project_result_renderable(
            "operations.project.status",
            {
                "workspace_id": "trader",
                "project_root": "/workspace/trader",
                "workspace_root": "/workspace/trader/.kairos",
            },
        )
    )

    assert "项目概览" in text
    assert "当前项目" in text
    assert "项目已打开" in text
    assert "'workspace_id'" not in text


def test_project_doctor_groups_repeated_resources_and_gives_next_steps() -> None:
    text = renderable_plain_text(
        project_result_renderable(
            "operations.project.doctor",
            {
                "ok": False,
                "ready": False,
                "issues": [
                    "launch aapl-paper: Workspace data connection is unavailable: primary-live: 'provider connection does not exist: primary-live'",
                    "launch btc-paper: Workspace data connection is unavailable: primary-live: 'provider connection does not exist: primary-live'",
                    "launch aapl-paper: Account requires a successful manual connection test: paper-account",
                ],
                "missing_directories": [],
                "launches": [{"launch_id": "aapl-paper"}, {"launch_id": "btc-paper"}],
            },
        )
    )

    assert "项目检查" in text
    assert "尚未就绪" in text
    assert "市场数据连接缺失" in text
    assert "primary-live" in text
    assert "2 个运行方案" in text
    assert "账户连接尚未验证" in text
    assert "建议下一步" in text
    assert "--format json" in text
    assert "'issues'" not in text


def test_project_doctor_explains_when_no_launch_is_available() -> None:
    text = renderable_plain_text(
        project_result_renderable(
            "operations.project.doctor",
            {
                "ok": True,
                "ready": False,
                "issues": [],
                "missing_directories": [],
                "launches": [],
            },
        )
    )

    assert "结构正常，但没有可运行方案" in text
    assert "安装项目模板或创建一个运行方案" in text
    assert "项目检查通过" not in text


def test_operations_service_selection_actions_and_back_use_one_input() -> None:
    async def run() -> tuple[type[object], str, str, str, bool, bool, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("6")
            await pilot.pause(0.1)
            assert screen.session.context == ("operations", "overview")
            screen.submit("1")
            await pilot.pause()
            assert screen.session.context == ("operations", "services")
            screen.submit("1")
            await pilot.pause()
            selected = str(screen.query_one("#command-context", Static).render())
            selected_actions = interaction_copy_text(screen.session.interaction)
            interaction = screen.session.interaction
            has_summary = (
                isinstance(interaction, ChoiceInteraction)
                and interaction.summary is not None
            )
            selected_status = str(screen.query_one("#command-status", Static).render())
            screen.submit("/back")
            screen.submit("1")
            await pilot.pause()
            services = str(screen.query_one("#command-context", Static).render())
            return (
                type(app.screen),
                selected,
                selected_actions,
                services,
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
                has_summary,
                selected_status,
            )

    (
        screen_type,
        selected,
        selected_actions,
        services,
        focused,
        has_summary,
        selected_status,
    ) = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert selected == "trader / 运行中心 / 标的服务  ›"
    assert "停止" in selected_actions
    assert "重启" in selected_actions
    assert "行情服务" not in selected_actions
    assert not has_summary
    assert "标的服务 · 运行中" in selected_status
    assert services == "trader / 运行中心 / 项目共享服务  ›"
    assert focused


def test_operations_center_instance_uses_shared_instance_detail() -> None:
    async def run() -> tuple[tuple[str, ...], str, int, tuple[str, ...]]:
        state = _state()
        assert state.snapshot is not None
        state.snapshot = ObserveSnapshot(
            workspace_id="trader",
            shared_services=state.snapshot.shared_services,
            active_instances=(
                {
                    "launch_id": "btc-paper",
                    "instance_id": "run-003",
                    "mode": "paper",
                    "state": "running",
                },
            ),
            support_processes=state.snapshot.support_processes,
        )
        app = KairosWorkbenchApp(state)
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("6")
            await pilot.pause(0.1)
            screen.submit("2")
            await pilot.pause()
            assert screen.session.context == ("operations", "instances")
            screen.submit("1")
            await pilot.pause()
            detail_context = screen.session.context
            copy = interaction_copy_text(screen.session.interaction)
            activity_count = len(screen._output().activities)
            screen.submit("/back")
            screen.submit("1")
            await pilot.pause()
            return (
                detail_context,
                copy,
                activity_count,
                screen.session.context,
            )

    context, copy, activity_count, after_back = asyncio.run(run())
    assert context == ("strategy", "instance")
    assert "btc-paper" in copy
    assert "实例概览" in copy
    assert activity_count == 0
    assert after_back == ("operations", "instances")


def test_support_process_detail_is_observable_but_not_lifecycle_control() -> None:
    async def run() -> tuple[str, str, str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("6")
            await pilot.pause(0.1)
            screen.submit("3")
            await pilot.pause()
            assert screen.session.context == ("operations", "supports")
            group_status = str(screen.query_one("#command-status", Static).render())
            screen.submit("1")
            await pilot.pause()
            detail = interaction_copy_text(screen.session.interaction)
            context = str(screen.query_one("#command-context", Static).render())
            screen.submit("/back")
            screen.submit("1")
            await pilot.pause()
            return (
                group_status,
                context,
                detail,
                str(screen.query_one("#command-context", Static).render()),
            )

    group_status, context, detail, after_back = asyncio.run(run())
    assert group_status == "就绪"
    assert context == "trader / 运行中心 / System Supervisor  ›"
    assert "System Supervisor" in detail
    assert "不提供普通服务启停" in detail
    assert "停止" not in detail
    assert "重启" not in detail
    assert after_back == "trader / 运行中心 / 支撑进程  ›"


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
    assert {item.id for item in service_actions(running)} >= {
        "stop",
        "restart",
        "market-routes",
        "market-subscriptions",
        "market-pause-replay",
        "market-resume-replay",
    }


def test_workspace_market_runtime_controls_live_only_in_operations_center() -> None:
    view = service_status_view(
        {"component": "market", "status": "ready", "control_reachable": True}
    )
    state = SimpleNamespace(
        owner=object(),
        load_error=None,
        dry_run=False,
        no_exec=False,
        yes=False,
    )
    session = GuidedSession(context=("operations", "service", "market"))
    session.operations.selected_service = "market"
    session.operations.selected_service_status = view

    route_effects = operations.handle_context(state, session, "r")
    assert route_effects is not None
    route = next(effect for effect in route_effects if isinstance(effect, RunOperation))
    assert route.operation.route.qualifier == "market-runtime:routes"

    pause_effects = operations.handle_context(state, session, "z")
    assert pause_effects is not None
    interaction = next(
        effect.interaction
        for effect in pause_effects
        if isinstance(effect, SetInteraction)
    )
    assert isinstance(interaction, ConfirmInteraction)
    assert "项目共享 Market" in interaction.operation.audit_summary


def test_stopped_service_detail_keeps_one_action_list_at_60x20() -> None:
    async def run() -> tuple[type[object], int, int, int, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(60, 20)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("6", "1", "2"):
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
            for value in ("6", "1", "2"):
                screen.submit(value)
                await pilot.pause(0.1)
            # The fixture marks Market stopped, so inject a running detail to
            # exercise the finite log-follow controls without a real process.
            screen.session.operations.selected_service_status = service_status_view(
                {
                    "component": "market",
                    "status": "ready",
                    "pid": 42,
                    "logs_available": True,
                }
            )
            screen._show_context()
            for value in ("5",):
                screen.submit(value)
                await pilot.pause(0.1)
            await pilot.pause(1.1)
            live_text = interaction_copy_text(screen.session.interaction)
            activity_count = len(screen._output().activities)
            screen.submit("/p")
            await pilot.pause(1.1)
            buffer = screen.session.operations.live_buffer
            assert buffer is not None
            unseen = buffer.unseen_lines
            screen.submit("/back")
            screen.submit("1")
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
    assert activity_count == 0
    assert unseen > 0
    assert refresh_count >= 2
    assert worker_closed
    assert "已结束行情服务日志跟随" in exported
    assert context == "trader / 运行中心 / 行情服务  ›"


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


def test_workspace_market_replay_control_lives_in_operations_center() -> None:
    async def run() -> tuple[type[object], str, str, ConfirmInteraction, bool]:
        state = _state()
        assert state.snapshot is not None
        state.snapshot.shared_services["market"] = {
            "status": "ready",
            "operating_mode": "continuous",
            "control_reachable": True,
            "pid_alive": True,
        }
        app = KairosWorkbenchApp(state)
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("6")
            await pilot.pause(0.1)
            screen.submit("1")
            screen.submit("2")
            screen.submit("z")
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
    assert context == "trader / 运行中心 / 行情服务  ›"
    assert output == ""
    assert "暂停项目共享 Market 行情回放" in interaction.operation.audit_summary
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
    assert context == "trader / 数据与回测 / 数据准备  ›"
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
