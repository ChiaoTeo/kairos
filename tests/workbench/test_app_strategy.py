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
from kairospy.investment.apps.account.application import AccountConfigurationApplication
from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
)
from kairospy.system.apps.integration.application import (
    ProviderConnectionConfigurationApplication,
)
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
from kairospy.surface.workbench.screens.session import GuidedSession
from kairospy.surface.workbench.screens.flows.launch import (
    execution,
    market as launch_market,
    runtime as strategy,
)
from kairospy.surface.workbench.screens.flows.launch.wizard import LaunchWizardState
from kairospy.surface.workbench.screens.flows.resources import (
    configuration as resources,
)
from kairospy.system.apps.observe.application import ObserveSnapshot
from kairospy.surface.workbench.widgets import (
    ActionList,
    ChoiceInteraction,
    ControlInteraction,
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


def test_strategy_launch_list_detail_and_back_stay_in_command_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        strategy,
        "load_launches",
        lambda state: (
            {
                "launch_id": "paper-demo",
                "mode": "paper",
                "state": "ready",
                "instance_id": "run-1",
                "config": "/workspace/launches/paper-demo.toml",
            },
        ),
    )

    async def run() -> tuple[type[object], str, str, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("2",):
                screen.submit(value)
                await pilot.pause(0.1)
            screen.submit("1")
            await pilot.pause()
            selected = str(screen.query_one("#command-context", Static).render())
            interaction = screen.session.interaction
            assert isinstance(interaction, ChoiceInteraction)
            assert interaction.summary is None
            assert not screen.query_one("#interaction-content", Static).display
            screen.submit("/back")
            screen.submit("1")
            await pilot.pause()
            launches = str(screen.query_one("#command-context", Static).render())
            return (
                type(app.screen),
                selected,
                launches,
                str(screen.session.strategy.selected_record["launch_id"]),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    screen_type, selected, launches, launch_id, focused = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert selected == "trader / 策略与运行 / 已选运行方案  ›"
    assert launches == "trader / 策略与运行 / 运行方案  ›"
    assert launch_id == "paper-demo"
    assert focused


def test_launch_instance_component_drilldown_stays_in_command_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        strategy,
        "load_launches",
        lambda state: ({"launch_id": "paper-demo", "mode": "paper"},),
    )
    monkeypatch.setattr(
        strategy,
        "load_instances",
        lambda state, launch_id: (
            {"instance_id": "run-1", "mode": "paper", "state": "running"},
        ),
    )
    monkeypatch.setattr(
        strategy,
        "load_components",
        lambda state, launch_id, instance_id, mode: (
            {"component": "risk", "status": "healthy", "pid": 42},
        ),
    )

    async def run() -> tuple[type[object], str, str, str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("2",):
                screen.submit(value)
                await pilot.pause(0.1)
            screen.submit("1")
            screen.submit("3")
            await pilot.pause(0.1)
            instances = str(screen.query_one("#command-context", Static).render())
            screen.submit("1")
            selected = str(screen.query_one("#command-context", Static).render())
            screen.submit("3")
            await pilot.pause(0.1)
            components = str(screen.query_one("#command-context", Static).render())
            screen.submit("1")
            screen.submit("/back")
            screen.submit("1")
            return (
                type(app.screen),
                instances,
                selected,
                components,
                str(screen.query_one("#command-context", Static).render()),
            )

    screen_type, instances, selected, components, after_back = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert instances == "trader / 策略与运行 / 运行实例  ›"
    assert selected == "trader / 策略与运行 / 已选实例  ›"
    assert components == "trader / 策略与运行 / 实例组件  ›"
    assert after_back == "trader / 策略与运行 / 已选实例  ›"


def test_completed_backtest_instance_exposes_report_on_the_shared_instance_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        strategy,
        "load_launches",
        lambda state: ({"launch_id": "backtest-demo", "mode": "backtest"},),
    )
    monkeypatch.setattr(
        strategy,
        "load_instances",
        lambda state, launch_id: (
            {
                "instance_id": "run-7",
                "mode": "backtest",
                "state": "completed",
            },
        ),
    )
    monkeypatch.setattr(
        strategy,
        "execute_launch",
        lambda state, record, action: (
            calls.append((str(record["instance_id"]), str(action)))
            or {"status": "completed", "report": "artifact://backtest/run-7"}
        ),
    )

    async def run() -> tuple[str, str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("2", "1", "3", "1"):
                screen.submit(value)
                await pilot.pause(0.08)
            instance_actions_copy = interaction_copy_text(screen.session.interaction)
            screen.submit("5")
            await pilot.pause(0.1)
            return (
                str(screen.query_one("#command-context", Static).render()),
                instance_actions_copy,
                _log_text(screen.query_one("#command-output", RichLog)),
            )

    context, actions, output = asyncio.run(run())
    assert calls == [("run-7", "report")]
    assert context == "trader / 策略与运行 / 已选实例  ›"
    assert "查看回测报告" in actions
    assert "report" in output


def test_run_readiness_repairs_resource_and_returns_to_retry_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = iter(
        (
            {
                "path": "/workspace/launches/paper-demo.toml",
                "valid": False,
                "issues": ["Workspace data connection is unavailable: primary"],
                "diagnostics": [
                    {
                        "owner": "Reference/Market",
                        "resource": "data_provider",
                        "severity": "blocker",
                        "reason": "Workspace data connection is unavailable: primary",
                        "action": "configure and manually test the selected data connection",
                    }
                ],
            },
            {
                "path": "/workspace/launches/paper-demo.toml",
                "valid": True,
                "issues": [],
                "diagnostics": [],
            },
        )
    )
    monkeypatch.setattr(
        strategy,
        "load_launches",
        lambda state: (
            {
                "launch_id": "paper-demo",
                "mode": "paper",
                "config": "/workspace/launches/paper-demo.toml",
            },
        ),
    )
    monkeypatch.setattr(
        strategy,
        "execute_launch",
        lambda state, record, action: next(reports),
    )
    monkeypatch.setattr(resources, "list_records", lambda state, kind: ())

    async def run() -> tuple[str, str, str, str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(110, 32)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("2", "1", "2"):
                screen.submit(value)
                await pilot.pause(0.08)
            readiness_context = str(
                screen.query_one("#command-context", Static).render()
            )
            readiness_copy = interaction_copy_text(screen.session.interaction)
            screen.submit("2")
            await pilot.pause(0.1)
            resource_context = str(
                screen.query_one("#command-context", Static).render()
            )
            screen.submit("/back")
            screen.submit("1")
            await pilot.pause(0.08)
            returned_context = str(
                screen.query_one("#command-context", Static).render()
            )
            screen.submit("1")
            await pilot.pause(0.1)
            return (
                readiness_context,
                readiness_copy,
                resource_context,
                returned_context,
                str(screen.query_one("#command-context", Static).render()),
            )

    readiness, actions, resource, returned, final = asyncio.run(run())
    assert readiness == "trader / 策略与运行 / 运行条件  ›"
    assert "修复行情连接" in actions
    assert resource == "trader / 连接与配置 / 市场数据  ›"
    assert returned == readiness
    assert final == "trader / 策略与运行 / 已选运行方案  ›"


def test_connected_execution_read_and_cancel_use_instance_scope_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, str]]] = []
    monkeypatch.setattr(
        strategy,
        "load_launches",
        lambda state: ({"launch_id": "paper-demo", "mode": "paper"},),
    )
    monkeypatch.setattr(
        strategy,
        "load_instances",
        lambda state, launch_id: (
            {"instance_id": "run-1", "mode": "paper", "state": "running"},
        ),
    )
    monkeypatch.setattr(
        strategy,
        "load_components",
        lambda state, launch_id, instance_id, mode: (
            {"component": "execution", "status": "healthy"},
        ),
    )

    def execute(state: object, prompt: object) -> dict[str, str]:
        calls.append((getattr(prompt, "action"), dict(getattr(prompt, "values"))))
        return {"status": "ok"}

    monkeypatch.setattr(
        execution,
        "execute_execution",
        execute,
    )

    async def run() -> tuple[type[object], str, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("2",):
                screen.submit(value)
                await pilot.pause(0.1)
            screen.submit("1")
            screen.submit("3")
            await pilot.pause(0.1)
            screen.submit("1")
            screen.submit("3")
            await pilot.pause(0.1)
            screen.submit("1")
            screen.submit("1")
            await pilot.pause(0.1)
            screen.submit("11")
            screen.submit("order-1")
            screen.submit("")
            assert [action for action, _ in calls] == ["status"]
            screen.submit("/confirm")
            await pilot.pause(0.1)
            return (
                type(app.screen),
                str(screen.query_one("#command-context", Static).render()),
                _log_text(screen.query_one("#command-output", RichLog)),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    screen_type, context, output, focused = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert [action for action, _ in calls] == ["status", "cancel"]
    assert calls[-1][1] == {"order-id": "order-1", "reason": "manual cancel"}
    assert context == "trader / 策略与运行 / Execution Server  ›"
    assert "Execution 作用域确认" not in output
    assert focused


def test_launch_market_snapshot_and_replay_pause_use_one_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, str]]] = []
    monkeypatch.setattr(
        strategy,
        "load_launches",
        lambda state: ({"launch_id": "backtest-demo", "mode": "backtest"},),
    )
    monkeypatch.setattr(
        strategy,
        "load_instances",
        lambda state, launch_id: (
            {"instance_id": "run-1", "mode": "backtest", "state": "running"},
        ),
    )
    monkeypatch.setattr(
        strategy,
        "load_components",
        lambda state, launch_id, instance_id, mode: (
            {"component": "market", "status": "healthy"},
        ),
    )

    def execute(state: object, prompt: object) -> dict[str, str]:
        calls.append((getattr(prompt, "action"), dict(getattr(prompt, "values"))))
        return {"status": "ok"}

    monkeypatch.setattr(
        launch_market,
        "execute_launch_market",
        execute,
    )

    async def run() -> tuple[str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("2",):
                screen.submit(value)
                await pilot.pause(0.1)
            screen.submit("1")
            screen.submit("3")
            await pilot.pause(0.1)
            screen.submit("1")
            screen.submit("3")
            await pilot.pause(0.1)
            screen.submit("1")
            screen.submit("3")
            screen.submit("market:btc-usdt")
            screen.submit("")
            await pilot.pause(0.1)
            screen.submit("/p")
            assert [action for action, _ in calls] == ["quote"]
            screen.submit("/confirm")
            await pilot.pause(0.1)
            return (
                str(screen.query_one("#command-context", Static).render()),
                _log_text(screen.query_one("#command-output", RichLog)),
            )

    context, output = asyncio.run(run())
    assert [action for action, _ in calls] == ["quote", "pause-replay"]
    assert calls[0][1]["market-id"] == "market:btc-usdt"
    assert context == "trader / 策略与运行 / Market 组件  ›"
    assert "Market quote 已返回当前实例结果" in output


def test_launch_timeline_export_uses_argument_and_inline_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exports: list[str] = []
    monkeypatch.setattr(
        strategy,
        "load_launches",
        lambda state: ({"launch_id": "paper-demo", "mode": "paper"},),
    )
    monkeypatch.setattr(
        strategy,
        "load_instances",
        lambda state, launch_id: (
            {"instance_id": "run-1", "mode": "paper", "state": "stopped"},
        ),
    )
    monkeypatch.setattr(
        strategy,
        "load_timeline",
        lambda state, launch_id, instance_id, mode: ({"event": "stopped"},),
    )
    monkeypatch.setattr(
        strategy,
        "export_timeline",
        lambda state, launch_id, instance_id, mode, destination: (
            exports.append(destination) or destination
        ),
    )

    async def run() -> tuple[str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("2",):
                screen.submit(value)
                await pilot.pause(0.1)
            screen.submit("1")
            screen.submit("3")
            await pilot.pause(0.1)
            screen.submit("1")
            screen.submit("4")
            await pilot.pause(0.1)
            screen.submit("2")
            screen.submit("timeline.jsonl")
            assert exports == []
            screen.submit("/confirm")
            await pilot.pause(0.1)
            return (
                str(screen.query_one("#command-context", Static).render()),
                _log_text(screen.query_one("#command-output", RichLog)),
            )

    context, output = asyncio.run(run())
    assert exports == ["timeline.jsonl"]
    assert context == "trader / 策略与运行 / 实例时间线  ›"
    assert "时间线导出结果已完成" in output


def test_launch_attach_python_uses_same_input_and_inline_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        strategy,
        "load_launches",
        lambda state: ({"launch_id": "paper-demo", "mode": "paper"},),
    )
    monkeypatch.setattr(
        strategy,
        "load_instances",
        lambda state, launch_id: (
            {"instance_id": "run-1", "mode": "paper", "state": "running"},
        ),
    )
    monkeypatch.setattr(
        strategy,
        "load_launch_attach_snapshot",
        lambda state, launch_id: {"status": "running", "logs": {"lines": []}},
    )
    monkeypatch.setattr(
        strategy,
        "send_python",
        lambda state, launch_id, source: (
            calls.append((launch_id, source)) or {"status": "accepted"}
        ),
    )

    async def run() -> tuple[str, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("2", "1", "3"):
                screen.submit(value)
                await pilot.pause(0.1)
            screen.submit("1")
            screen.submit("2")
            await pilot.pause(0.1)
            screen.submit("4")
            screen.submit("print('ready')")
            assert calls == []
            screen.submit("/confirm")
            await pilot.pause(0.1)
            return (
                str(screen.query_one("#command-context", Static).render()),
                _log_text(screen.query_one("#command-output", RichLog)),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    context, output, focused = asyncio.run(run())
    assert calls == [("paper-demo", "print('ready')")]
    assert context == "trader / 策略与运行 / 跟随输出  ›"
    assert "accepted" in output
    assert focused


def test_launch_attach_background_refresh_deduplicates_logs_and_can_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refreshes: list[str] = []
    monkeypatch.setattr(
        strategy,
        "load_launches",
        lambda state: ({"launch_id": "paper-demo", "mode": "paper"},),
    )
    monkeypatch.setattr(
        strategy,
        "load_instances",
        lambda state, launch_id: (
            {"instance_id": "run-1", "mode": "paper", "state": "running"},
        ),
    )

    def snapshot(state: object, launch_id: str) -> dict[str, object]:
        refreshes.append(launch_id)
        return {
            "instance": {"instance_id": "run-1"},
            "status": {"state": "running"},
            "logs": {
                "latest": "/workspace/logs/strategy/process.log",
                "lines": [
                    f"strategy-log-{index}" for index in range(1, len(refreshes) + 1)
                ],
            },
        }

    monkeypatch.setattr(strategy, "load_launch_attach_snapshot", snapshot)

    async def run() -> tuple[int, int, int, int, str, str, ControlInteraction]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("2", "1", "3"):
                screen.submit(value)
                await pilot.pause(0.1)
            screen.submit("1")
            screen.submit("2")
            await pilot.pause(1.2)
            before_pause = len(refreshes)
            screen.submit("2")
            await pilot.pause(1.2)
            while_paused = len(refreshes)
            live_buffer = screen.session.strategy.live_buffer
            assert live_buffer is not None
            unseen_while_paused = live_buffer.unseen_lines
            screen.submit("2")
            await pilot.pause(0.2)
            interaction = screen.session.interaction
            assert isinstance(interaction, ControlInteraction)
            return (
                before_pause,
                while_paused,
                len(refreshes),
                unseen_while_paused,
                _log_text(screen.query_one("#command-output", RichLog)),
                interaction_copy_text(interaction),
                interaction,
            )

    (
        before_pause,
        while_paused,
        after_resume,
        unseen_while_paused,
        output,
        control_text,
        interaction,
    ) = asyncio.run(run())
    assert before_pause >= 2
    assert while_paused > before_pause
    assert unseen_while_paused > 0
    assert after_resume > while_paused
    assert "strategy-log" not in output
    assert "strategy-log" in control_text
    assert "/workspace/logs/strategy/process.log" in control_text
    assert "Strategy Runtime" in control_text
    assert "内置连接" in control_text
    assert "Market 订阅" in control_text
    assert "数据流" in control_text
    assert "最近数据" in control_text
    assert "Launch 状态刷新" not in output
    assert interaction.refreshing


def test_launch_attach_clear_only_removes_visible_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        strategy,
        "load_launches",
        lambda state: ({"launch_id": "paper-demo", "mode": "paper"},),
    )
    monkeypatch.setattr(
        strategy,
        "load_instances",
        lambda state, launch_id: (
            {"instance_id": "run-1", "mode": "paper", "state": "running"},
        ),
    )
    monkeypatch.setattr(
        strategy,
        "load_launch_attach_snapshot",
        lambda *args: {
            "instance": {"instance_id": "run-1"},
            "status": {"state": "running"},
            "logs": {
                "latest": "/workspace/logs/strategy/process.log",
                "lines": ["line-one", "line-two"],
            },
        },
    )

    async def run() -> tuple[str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("2", "1", "3", "1", "2"):
                screen.submit(value)
                await pilot.pause(0.1)
            screen.submit("3")
            await pilot.pause()
            live_buffer = screen.session.strategy.live_buffer
            assert live_buffer is not None
            interaction = screen.session.interaction
            assert isinstance(interaction, ControlInteraction)
            return live_buffer.copy_text(), interaction_copy_text(interaction)

    visible_lines, control_text = asyncio.run(run())

    assert visible_lines == ""
    assert "line-one" not in control_text
    assert "/workspace/logs/strategy/process.log" in control_text


def test_launch_attach_drops_result_from_an_older_navigation_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    calls = 0
    monkeypatch.setattr(
        strategy,
        "load_launches",
        lambda state: ({"launch_id": "paper-demo", "mode": "paper"},),
    )
    monkeypatch.setattr(
        strategy,
        "load_instances",
        lambda state, launch_id: (
            {"instance_id": "run-1", "mode": "paper", "state": "running"},
        ),
    )

    def snapshot(state: object, launch_id: str) -> dict[str, object]:
        nonlocal calls
        calls += 1
        marker = "fresh-result"
        if calls == 1:
            started.set()
            release.wait(timeout=2)
            marker = "stale-result"
        return {
            "instance": {"instance_id": "run-1"},
            "status": {"state": "running", "marker": marker},
            "logs": {"lines": [marker]},
        }

    monkeypatch.setattr(strategy, "load_launch_attach_snapshot", snapshot)

    async def run() -> tuple[int, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("2", "1", "3", "1", "2"):
                screen.submit(value)
                await pilot.pause(0.1)
            for _ in range(20):
                if started.is_set():
                    break
                await pilot.pause(0.05)
            screen.submit("/back")
            await pilot.pause(0.1)
            screen.submit("1")
            screen.submit("2")
            release.set()
            await pilot.pause(1.3)
            return calls, interaction_copy_text(screen.session.interaction)

    refresh_count, control_text = asyncio.run(run())
    release.set()

    assert refresh_count >= 2
    assert "fresh-result" in control_text
    assert "stale-result" not in control_text


def test_launch_new_wizard_collects_fields_and_confirms_draft_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        strategy,
        "load_launches",
        lambda state: (),
    )
    monkeypatch.setattr(
        strategy,
        "open_new_launch_wizard",
        lambda state, launch_id: LaunchWizardState(launch_id),
    )
    monkeypatch.setattr(
        strategy,
        "save_launch_wizard",
        lambda state, wizard, publish: (
            saved.append((wizard.launch_id, publish))
            or {"status": "draft", "ready": True}
        ),
    )

    async def run() -> tuple[type[object], str, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("2")
            await pilot.pause(0.1)
            screen.submit("/new")
            screen.submit("backtest-demo")
            for value in (
                "1",
                "",
                "2",
                "",
                "",
                "",
                "",
                "",
                "draft",
            ):
                screen.submit(value)
            assert saved == []
            screen.submit("/confirm")
            await pilot.pause(0.1)
            return (
                type(app.screen),
                str(screen.query_one("#command-context", Static).render()),
                _log_text(screen.query_one("#command-output", RichLog)),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    screen_type, context, output, focused = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert saved == [("backtest-demo", False)]
    assert context == "trader / 策略与运行 / 已选运行方案  ›"
    assert "Launch 脱敏摘要" not in output
    assert "Launch 配置已就绪" in output
    assert focused


def test_launch_wizard_preserves_unmanaged_advanced_values() -> None:
    wizard = LaunchWizardState(
        "paper-demo",
        values={
            "launch": {"id": "paper-demo", "mode": "paper"},
            "advanced": {"retained": "yes"},
        },
    )
    while (prompt := wizard.next_prompt()) is not None:
        wizard.accept(prompt[0], "")
    assert wizard.build_values()["advanced"] == {"retained": "yes"}


def test_launch_wizard_preserves_agent_review_profile_and_capability_fields() -> None:
    wizard = LaunchWizardState(
        "paper-demo",
        values={
            "launch": {
                "id": "paper-demo",
                "mode": "paper",
                "strategy": "builtin:test",
            },
            "agent": {
                "enabled": True,
                "required": False,
                "profile": {
                    "version": "2",
                    "goal": "bounded review",
                    "rubric": ["bounded"],
                    "invalidation_rules": ["stale"],
                    "reason_codes": ["ok"],
                    "risk_flags": ["high"],
                },
                "model": {"connection": "local", "model": "qwen"},
                "capabilities": {
                    "intent_review": {
                        "initial_mode": "gate",
                        "strategy_selectable_modes": ["shadow", "gate"],
                        "operations": ["target_position"],
                        "required_contexts": ["account"],
                    }
                },
            },
        },
    )
    while (prompt := wizard.next_prompt()) is not None:
        wizard.accept(prompt[0], "")

    agent = wizard.build_values()["agent"]
    assert agent["profile"] == {
        "version": "2",
        "goal": "bounded review",
        "rubric": ["bounded"],
        "invalidation_rules": ["stale"],
        "reason_codes": ["ok"],
        "risk_flags": ["high"],
    }
    assert agent["capabilities"]["intent_review"]["strategy_selectable_modes"] == [
        "shadow",
        "gate",
    ]
    assert agent["capabilities"]["intent_review"]["required_contexts"] == ["account"]


def test_launch_wizard_selects_one_verified_model_ref(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="launch-model"
    )
    connections = ModelProviderConnectionApplication(workspace)
    connections.configure(
        "ollama-local", provider="ollama", models=("qwen3:8b", "untested:latest")
    )
    connections.test("ollama-local", "qwen3:8b", probe=lambda *_args: {"ok": True})
    state = WorkbenchState(owner=workspace, workspace_arg=workspace.paths.root)
    wizard = LaunchWizardState("paper-model")
    while (prompt := wizard.next_prompt()) is not None:
        if prompt[0] == "agent-enabled":
            wizard.accept(prompt[0], "yes")
        elif prompt[0] == "agent-model-ref":
            break
        else:
            wizard.accept(prompt[0], "")
    session = GuidedSession(context=("strategy", "setup"))
    session.strategy.wizard = wizard

    effects = strategy._advance_wizard(state, session, wizard)

    assert effects
    interaction = session.interaction
    assert isinstance(interaction, ChoiceInteraction)
    assert [action.label for action in interaction.actions] == ["ollama-local/qwen3:8b"]
    strategy.handle_context(state, session, "1")
    assert wizard.answers["agent-model-ref"] == "ollama-local/qwen3:8b"
    while (prompt := wizard.next_prompt()) is not None:
        wizard.accept(prompt[0], "")
    values = wizard.build_values()
    assert values["agent"]["model"] == {
        "connection": "ollama-local",
        "model": "qwen3:8b",
    }


def test_live_launch_wizard_selects_business_resources_and_access_mode(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="launch-resources"
    )
    AccountConfigurationApplication(workspace).simulate("paper-main")
    CredentialConfigurationApplication(workspace).configure(
        "massive-read", provider="massive", values={"api_key": "secret"}
    )
    ProviderConnectionConfigurationApplication(workspace).configure(
        "massive-equity",
        provider="massive",
        credential_id="massive-read",
        products=("equity",),
        purposes=("market-query",),
    )
    state = WorkbenchState(owner=workspace, workspace_arg=workspace.paths.root)
    wizard = LaunchWizardState("live-resources")
    session = GuidedSession(context=("strategy", "setup"))
    session.strategy.wizard = wizard

    strategy._advance_wizard(state, session, wizard)
    strategy.handle_context(state, session, "3")
    wizard.accept("strategy", "builtin:interactive")
    strategy._advance_wizard(state, session, wizard)
    assert isinstance(session.interaction, ChoiceInteraction)
    assert "paper-main" in [action.label for action in session.interaction.actions]

    strategy.handle_context(state, session, "3")
    strategy.handle_context(state, session, "1")
    assert wizard.answers["accounts"] == ("paper-main",)
    assert wizard.next_prompt()[0] == "market-profile"
    assert isinstance(session.interaction, ChoiceInteraction)
    assert [action.label for action in session.interaction.actions] == [
        "massive-equity"
    ]

    strategy.handle_context(state, session, "1")
    while (prompt := wizard.next_prompt()) is not None:
        if prompt[0] == "live-trading":
            break
        wizard.accept(prompt[0], "")
    strategy._advance_wizard(state, session, wizard)
    assert [action.label for action in session.interaction.actions] == [
        "只读观察",
        "允许交易",
    ]
    strategy.handle_context(state, session, "1")
    assert wizard.answers["live-trading"] is False
    assert "account-scopes" not in wizard._step_names()


@pytest.mark.parametrize(
    ("app_kwargs", "launch_id", "expected_context"),
    (
        (
            {"initial_launch_attach": "paper-demo"},
            "paper-demo",
            "trader / 策略与运行 / 跟随输出  ›",
        ),
        (
            {"initial_launch_setup": ("new-demo", None)},
            "new-demo",
            "trader / 策略与运行 / 配置向导  ›",
        ),
    ),
)
def test_launch_deep_links_enter_same_command_screen(
    app_kwargs: dict[str, object], launch_id: str, expected_context: str
) -> None:
    async def run() -> tuple[type[object], str, str, bool]:
        app = KairosWorkbenchApp(_state(), **app_kwargs)  # type: ignore[arg-type]
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            return (
                type(screen),
                str(screen.query_one("#command-context", Static).render()),
                str(screen.session.strategy.selected_record["launch_id"]),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    screen_type, context, selected, focused = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert context == expected_context
    assert selected == launch_id
    assert focused
