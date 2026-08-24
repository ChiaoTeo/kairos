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


def test_resource_list_detail_and_back_stay_in_command_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.command_line.list_resource_records",
        lambda state, kind: (
            {
                "account_id": "paper-main",
                "provider": "binance",
                "enabled": True,
                "verification_status": "verified",
            },
        ),
    )

    async def run() -> tuple[type[object], str, str, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("4", "1"):
                screen.submit(value)
                await pilot.pause(0.1)
            screen.submit("1")
            await pilot.pause()
            selected = str(screen.query_one("#command-context", Static).render())
            screen.submit("/back")
            await pilot.pause()
            results = str(screen.query_one("#command-context", Static).render())
            return (
                type(app.screen),
                selected,
                results,
                app.state.selected_account or "",
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    screen_type, selected, results, account, focused = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert selected == "首页 / 运行准备 / 已选运行资源  ›"
    assert results == "首页 / 运行准备 / 查询结果  ›"
    assert account == "paper-main"
    assert focused


def test_account_runtime_queries_and_fee_argument_stay_in_resource_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None]] = []
    record = {
        "account_id": "paper-main",
        "provider": "binance",
        "enabled": True,
        "verification_status": "verified",
    }
    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.command_line.list_resource_records",
        lambda state, kind: (record,),
    )
    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.command_line.execute_account_action",
        lambda state, selected, action, value=None: (
            calls.append((action, value)) or {"action": action, "account": "paper-main"}
        ),
    )

    async def run() -> tuple[type[object], str, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("4", "1"):
                screen.submit(value)
                await pilot.pause(0.1)
            for value in ("1", "1", "2"):
                screen.submit(value)
            await pilot.pause(0.1)
            screen.submit("6")
            screen.submit("perpetual:BTCUSDT")
            await pilot.pause(0.1)
            return (
                type(app.screen),
                str(screen.query_one("#command-context", Static).render()),
                _log_text(screen.query_one("#command-output", RichLog)),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    screen_type, context, output, focused = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert calls == [("assets", None), ("fees", "perpetual:BTCUSDT")]
    assert context == "首页 / 运行准备 / 账户运行查询  ›"
    assert "账户运行结果" in output
    assert focused


def test_account_order_read_and_submit_confirmation_use_one_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, str]]] = []
    record = {
        "account_id": "paper-main",
        "broker": "binance",
        "environment": "paper",
        "segments": ["spot"],
        "verification_status": "verified",
    }
    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.command_line.list_resource_records",
        lambda state, kind: (record,),
    )

    def execute(state: object, prompt: object) -> dict[str, object]:
        action = getattr(prompt, "action")
        values = dict(getattr(prompt, "values"))
        calls.append((action, values))
        return {"action": action, "orders": []}

    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.command_line.execute_order", execute
    )

    async def run() -> tuple[type[object], str, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("4", "1"):
                screen.submit(value)
                await pilot.pause(0.1)
            for value in ("1", "1", "4", "1"):
                screen.submit(value)
            await pilot.pause(0.1)
            screen.submit("5")
            for value in ("order-1", "BTC-USDT", "", "0.01", "", ""):
                screen.submit(value)
            assert [action for action, _ in calls] == ["open-orders"]
            screen.submit("/confirm")
            await pilot.pause(0.1)
            assert screen.session.selected_resource is not None
            return (
                type(app.screen),
                str(screen.query_one("#command-context", Static).render()),
                _log_text(screen.query_one("#command-output", RichLog)),
                screen.query_one("#command-input", WorkbenchCommandInput).has_focus,
            )

    screen_type, context, output, focused = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert [action for action, _ in calls] == ["open-orders", "submit"]
    assert calls[-1][1]["side"] == "buy"
    assert calls[-1][1]["order-type"] == "market"
    assert context == "首页 / 运行准备 / 订单管理  ›"
    assert "订单作用域确认" in output
    assert "订单操作结果" in output
    assert focused


def test_resource_toggle_uses_inline_confirmation_and_preserves_one_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    record = {
        "account_id": "paper-main",
        "provider": "binance",
        "enabled": True,
        "verification_status": "verified",
    }
    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.command_line.list_resource_records",
        lambda state, kind: (record,),
    )
    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.command_line.execute_resource_action",
        lambda state, kind, selected, action, **kwargs: (
            calls.append((kind, action)) or {**selected, "enabled": False}
        ),
    )

    async def run() -> tuple[type[object], bool, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("4", "1"):
                screen.submit(value)
                await pilot.pause(0.1)
            screen.submit("1")
            screen.submit("5")
            assert calls == []
            screen.submit("/confirm")
            await pilot.pause(0.1)
            selected = screen.session.selected_resource
            assert selected is not None
            return (
                type(app.screen),
                bool(selected["enabled"]),
                _log_text(screen.query_one("#command-output", RichLog)),
            )

    screen_type, enabled, output = asyncio.run(run())
    assert screen_type is CommandLineScreen
    assert calls == [("accounts", "toggle")]
    assert not enabled
    assert "资源操作结果" in output


def test_notification_attach_collects_launch_and_route_before_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None, str | None]] = []
    record = {
        "destination_id": "ops-alerts",
        "sender": "feishu",
        "enabled": True,
        "verification_status": "verified",
    }
    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.command_line.list_resource_records",
        lambda state, kind: (record,),
    )

    def execute(
        state: object,
        kind: str,
        selected: dict[str, object],
        action: str,
        *,
        value: str | None = None,
        launch_id: str | None = None,
    ) -> dict[str, str]:
        calls.append((action, value, launch_id))
        return {"status": "attached"}

    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.command_line.execute_resource_action",
        execute,
    )

    async def run() -> tuple[str | None, str | None, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("4", "4"):
                screen.submit(value)
                await pilot.pause(0.1)
            for value in ("1", "2", "launch-alpha", "alerts"):
                screen.submit(value)
            assert calls == []
            screen.submit("/confirm")
            await pilot.pause(0.1)
            return (
                screen.session.resource_action,
                screen.session.resource_launch_id,
                screen.session.prompt_mode.value,
            )

    action, launch_id, mode = asyncio.run(run())
    assert calls == [("attach", "alerts", "launch-alpha")]
    assert action is None
    assert launch_id is None
    assert mode == "navigation"


def test_resource_delete_dry_run_previews_without_executing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = {
        "account_id": "paper-main",
        "provider": "binance",
        "enabled": True,
        "verification_status": "verified",
    }
    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.command_line.list_resource_records",
        lambda state, kind: (record,),
    )

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry-run must not execute the resource deletion")

    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.command_line.execute_resource_action",
        unexpected,
    )

    async def run() -> tuple[str, tuple[dict[str, Any], ...]]:
        state = _state()
        state.dry_run = True
        app = KairosWorkbenchApp(state)
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("4", "1"):
                screen.submit(value)
                await pilot.pause(0.1)
            screen.submit("1")
            screen.submit("6")
            await pilot.pause(0.1)
            return (
                _log_text(screen.query_one("#command-output", RichLog)),
                screen.session.visible_records,
            )

    output, records = asyncio.run(run())
    assert "preview" in output
    assert records == ()


def test_resource_setup_uses_masked_single_input_and_never_records_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[str] = []
    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.command_line.list_resource_records",
        lambda state, kind: (),
    )

    def save(state: object, wizard: object) -> dict[str, object]:
        answers = getattr(wizard, "answers")
        saved.append(str(answers["secret-primary"]))
        return {
            "connection_id": "massive-main",
            "provider": "massive",
            "enabled": True,
        }

    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.command_line.save_resource_wizard",
        save,
    )

    async def run() -> tuple[bool, bool, str, str, tuple[str, ...]]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("4")
            screen.submit("2")
            await pilot.pause(0.1)
            screen.submit("/new")
            screen.submit("massive-main")
            screen.submit("")
            screen.submit("")
            command_input = screen.query_one("#command-input", WorkbenchCommandInput)
            masked = command_input.password
            command_input.value = "top-secret-value"
            await pilot.press("enter")
            await pilot.pause()
            unmasked = not command_input.password
            assert saved == []
            screen.submit("/confirm")
            await pilot.pause(0.1)
            return (
                masked,
                unmasked,
                str(app.transcript.events),
                _log_text(screen.query_one("#command-output", RichLog)),
                tuple(command_input._history),
            )

    masked, unmasked, transcript, output, history = asyncio.run(run())
    assert saved == ["top-secret-value"]
    assert masked
    assert unmasked
    assert "top-secret-value" not in transcript
    assert "top-secret-value" not in output
    assert "top-secret-value" not in history
    assert "<redacted>" in transcript


def test_ctrl_c_during_resource_secret_prompt_clears_staged_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.command_line.list_resource_records",
        lambda state, kind: (),
    )

    async def run() -> tuple[object | None, bool, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("4")
            screen.submit("2")
            await pilot.pause(0.1)
            for value in ("/new", "massive-main", "", ""):
                screen.submit(value)
            assert screen.query_one("#command-input", WorkbenchCommandInput).password
            await pilot.press("ctrl+c")
            await pilot.pause()
            return (
                screen.session.resource_wizard,
                screen.query_one("#command-input", WorkbenchCommandInput).password,
                str(screen.query_one("#command-context", Static).render()),
            )

    wizard, password, context = asyncio.run(run())
    assert wizard is None
    assert not password
    assert context == "首页 / 运行准备 / 查询结果  ›"
