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
from kairospy.surface.workbench.screens.flows import resources_account
from kairospy.surface.workbench.screens.guided.account import execute as execute_account
from kairospy.surface.workbench.screens.guided.resource_wizard import (
    ResourceWizardState,
    save_resource_wizard,
)
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
        resources_account,
        "list_records",
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
    assert selected == "首页 / 运行准备 / 已选运行资源 · 交易账户 · paper-main  ›"
    assert results == "首页 / 运行准备 / 交易账户  ›"
    assert account == "paper-main"
    assert focused


def test_account_resource_list_uses_a_human_summary_instead_of_raw_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = {
        "account_id": "manual-live-readonly",
        "broker": "binance",
        "environment": "live",
        "segments": ["funding", "spot", "usd_m_futures"],
        "credential_role": "readonly",
        "status": "configured",
        "verification_status": "pending",
        "tested_configuration_hash": None,
    }
    monkeypatch.setattr(
        resources_account,
        "list_records",
        lambda state, kind: (record,),
    )

    async def run() -> str:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(120, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("4")
            screen.submit("1")
            await pilot.pause(0.1)
            action = screen.query_one("#guided-actions", ActionList)._options[0]
            return str(action.prompt)

    prompt = asyncio.run(run())
    assert prompt == (
        "[1]  manual-live-readonly  ·  "
        "binance · live · funding/spot/usd_m_futures · readonly · 待验证"
    )
    assert "account_id" not in prompt
    assert "{" not in prompt


def test_each_runtime_resource_keeps_kind_and_identity_in_its_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {
        "accounts": {"account_id": "paper-main", "broker": "binance"},
        "data": {"connection_id": "massive", "provider": "massive"},
        "models": {"connection_id": "openai-main", "provider": "openai"},
        "notifications": {
            "destination_id": "ops-alerts",
            "provider": "feishu",
        },
    }
    labels = {
        "accounts": "交易账户",
        "data": "市场数据",
        "models": "AI 模型",
        "notifications": "通知提醒",
    }
    monkeypatch.setattr(
        resources_account,
        "list_records",
        lambda state, kind: (records[kind],),
    )

    async def run() -> list[tuple[str, str]]:
        contexts: list[tuple[str, str]] = []
        for shortcut, kind in enumerate(records, 1):
            app = KairosWorkbenchApp(_state())
            async with app.run_test(size=(100, 30)) as pilot:
                screen = app.screen
                assert isinstance(screen, CommandLineScreen)
                screen.submit("4")
                screen.submit(str(shortcut))
                await pilot.pause(0.1)
                listed = str(screen.query_one("#command-context", Static).render())
                screen.submit("1")
                await pilot.pause()
                selected = str(screen.query_one("#command-context", Static).render())
                contexts.append((listed, selected))
        return contexts

    contexts = asyncio.run(run())
    for (kind, record), (listed, selected) in zip(records.items(), contexts):
        resource_id = next(
            str(record[key])
            for key in ("account_id", "connection_id", "destination_id")
            if key in record
        )
        assert listed == f"首页 / 运行准备 / {labels[kind]}  ›"
        assert selected == (
            f"首页 / 运行准备 / 已选运行资源 · {labels[kind]} · {resource_id}  ›"
        )


def test_check_all_connections_renders_all_resource_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resources_account,
        "summary",
        lambda state: {
            "accounts": (1, 1),
            "data": (1, 0),
            "models": (0, 0),
            "notifications": (1, 1),
        },
    )

    async def run() -> tuple[str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("4")
            screen.submit("5")
            await pilot.pause(0.1)
            return (
                str(screen.query_one("#command-context", Static).render()),
                _log_text(screen.query_one("#command-output", RichLog)),
            )

    context, output = asyncio.run(run())
    assert context == "首页 / 运行准备  ›"
    assert "运行资源检查" in output
    for label in ("交易账户", "市场数据", "AI 模型", "通知提醒"):
        assert label in output


def test_empty_resource_groups_offer_new_configuration_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resources_account,
        "list_records",
        lambda state, kind: (),
    )
    labels = ("交易账户", "市场数据", "AI 模型", "通知提醒")

    async def run() -> list[tuple[str, str, str, int]]:
        states: list[tuple[str, str, str, int]] = []
        for shortcut in range(1, 5):
            app = KairosWorkbenchApp(_state())
            async with app.run_test(size=(100, 30)) as pilot:
                screen = app.screen
                assert isinstance(screen, CommandLineScreen)
                screen.submit("4")
                screen.submit(str(shortcut))
                await pilot.pause(0.1)
                actions = screen.query_one("#guided-actions", ActionList)
                states.append(
                    (
                        str(screen.query_one("#command-context", Static).render()),
                        str(screen.query_one("#command-status", Static).render()),
                        str(actions._options[0].prompt),
                        actions.option_count,
                    )
                )
        return states

    states = asyncio.run(run())
    for label, (context, status, action, count) in zip(labels, states):
        assert context == f"首页 / 运行准备 / {label}  ›"
        assert status == f"尚未配置 {label}"
        assert f"添加{label}" in action
        assert count == 1


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
        resources_account,
        "list_records",
        lambda state, kind: (record,),
    )
    monkeypatch.setattr(
        resources_account,
        "execute_account",
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
    assert context == "首页 / 运行准备 / 账户运行查询 · paper-main  ›"
    assert "paper-main · 账户运行结果" in output
    assert focused


def test_account_runtime_queries_bind_selected_account_as_a_global_cli_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.guided.account.AccountCliApplication.run",
        lambda application, arguments: calls.append(tuple(arguments)) or {},
    )
    state = _state()
    record = {"account_id": "manual-live-readonly"}

    for action in ("overview", "assets", "positions", "earn"):
        execute_account(state, record, action)
    execute_account(state, record, "fees", "spot:AAPLBUSDT")

    prefix = ("--account-id", "manual-live-readonly", "standalone")
    assert calls == [
        (*prefix, "overview"),
        (*prefix, "assets"),
        (*prefix, "positions"),
        (*prefix, "earn-holdings"),
        (*prefix, "fees", "--product", "spot", "--symbol", "AAPLBUSDT"),
    ]


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
        resources_account,
        "list_records",
        lambda state, kind: (record,),
    )

    def execute(state: object, prompt: object) -> dict[str, object]:
        action = getattr(prompt, "action")
        values = dict(getattr(prompt, "values"))
        calls.append((action, values))
        return {"action": action, "orders": []}

    monkeypatch.setattr(resources_account, "execute_order", execute)

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
            assert screen.session.resources.selected is not None
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
    assert context == "首页 / 运行准备 / 订单管理 · paper-main  ›"
    assert "订单作用域确认" not in output
    assert "paper-main · 订单操作结果" in output
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
        resources_account,
        "list_records",
        lambda state, kind: (record,),
    )
    monkeypatch.setattr(
        resources_account,
        "execute_action",
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
            selected = screen.session.resources.selected
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
        resources_account,
        "list_records",
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
        resources_account,
        "execute_action",
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
                screen.session.resources.action,
                screen.session.resources.launch_id,
                screen.session.interaction.mode.value,
            )

    action, launch_id, mode = asyncio.run(run())
    assert calls == [("attach", "alerts", "launch-alpha")]
    assert action is None
    assert launch_id is None
    assert mode == "choice"


def test_existing_notification_can_enter_identity_preserving_edit_wizard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = {
        "destination_id": "ops-alerts",
        "provider": "feishu",
        "credential_id": "ops-alerts",
        "enabled": True,
    }
    monkeypatch.setattr(
        resources_account,
        "list_records",
        lambda state, kind: (record,),
    )

    async def run() -> tuple[str, str, bool]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("4")
            screen.submit("4")
            await pilot.pause(0.1)
            screen.submit("1")
            screen.submit("5")
            await pilot.pause()
            return (
                str(screen.query_one("#command-context", Static).render()),
                screen.query_one("#command-input", WorkbenchCommandInput).placeholder,
                isinstance(screen.session.resources.wizard, ResourceWizardState),
            )

    context, placeholder, has_wizard = asyncio.run(run())
    assert context == "首页 / 运行准备 / 配置向导 · 通知提醒 · ops-alerts  ›"
    assert placeholder == "通知渠道（feishu / telegram）"
    assert has_wizard


def test_model_wizard_changes_provider_specific_defaults() -> None:
    wizard = ResourceWizardState(
        "models",
        {
            "connection_id": "primary-model",
            "provider": "openai",
            "api_mode": "openai-responses",
            "base_url": "https://api.openai.com/v1",
        },
    )
    wizard.accept("model-provider", "anthropic")

    assert wizard._default("model-mode") == "anthropic-messages"
    assert wizard._default("endpoint") == "https://api.anthropic.com/v1"


def test_editing_market_data_preserves_its_credential_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_credentials: list[str] = []
    configured_connections: list[str] = []

    class Credentials:
        def __init__(self, owner: object) -> None:
            pass

        def configure_secret_values(self, credential_id: str, **kwargs: object) -> None:
            configured_credentials.append(credential_id)

    class Reference:
        def __init__(self, owner: object) -> None:
            pass

        def configure_massive(
            self, *, credential_id: str, **kwargs: object
        ) -> dict[str, str]:
            configured_connections.append(credential_id)
            return {"connection_id": "massive", "credential_id": credential_id}

    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.guided.resource_wizard.CredentialConfigurationApplication",
        Credentials,
    )
    monkeypatch.setattr(
        "kairospy.surface.workbench.screens.guided.resource_wizard.ReferenceProviderConfigurationApplication",
        Reference,
    )
    wizard = ResourceWizardState(
        "data",
        {
            "connection_id": "massive",
            "credential_id": "massive-readonly",
            "endpoint": "https://api.massive.com",
            "capabilities": ["reference", "equity_market"],
        },
        {
            "endpoint": "https://api.massive.com",
            "include-options": False,
            "secret-primary": "replacement-key",
        },
    )

    save_resource_wizard(_state(), wizard)

    assert configured_credentials == ["massive-readonly"]
    assert configured_connections == ["massive-readonly"]


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
        resources_account,
        "list_records",
        lambda state, kind: (record,),
    )

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry-run must not execute the resource deletion")

    monkeypatch.setattr(
        resources_account,
        "execute_action",
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
        resources_account,
        "list_records",
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
        resources_account,
        "save_resource_wizard",
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
        resources_account,
        "list_records",
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
                screen.session.resources.wizard,
                screen.query_one("#command-input", WorkbenchCommandInput).password,
                str(screen.query_one("#command-context", Static).render()),
            )

    wizard, password, context = asyncio.run(run())
    assert wizard is None
    assert not password
    assert context == "首页 / 运行准备 / 市场数据  ›"
