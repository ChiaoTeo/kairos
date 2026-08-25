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
from kairospy.surface.workbench.screens.flows.resources import (
    account,
    configuration as resources,
)
from kairospy.surface.workbench.screens.flows.resources.account_actions import (
    execute as execute_account,
)
import kairospy.surface.workbench.screens.flows.resources.account_actions as account_actions
import kairospy.surface.workbench.screens.flows.resources.wizard as resource_wizard
from kairospy.surface.workbench.screens.flows.resources.wizard import (
    ResourceWizardState,
    save_resource_wizard,
)
from kairospy.surface.workbench.screens.flows.resources.actions import detail_actions
from kairospy.surface.workbench.screens.flows.resources.views import (
    action_result_renderable,
    detail_renderable,
)
from kairospy.surface.workbench.screens.flows.launch.wizard import LaunchWizardState
from kairospy.system.apps.observe.application import ObserveSnapshot
from kairospy.surface.workbench.widgets import (
    ActionList,
    ChoiceInteraction,
    InputInteraction,
    WorkbenchCommandInput,
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


def test_resource_list_detail_and_back_stay_in_command_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resources,
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
                str(screen.session.resources.selected["account_id"]),
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
        resources,
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


def test_notification_detail_prioritizes_user_facing_fields() -> None:
    content = renderable_plain_text(
        detail_renderable(
            "notifications",
            {
                "destination_id": "telegram-alerts",
                "provider": "telegram",
                "enabled": True,
                "credential_id": "telegram-alerts",
                "secret_available": True,
                "chat_id": "5705864725",
                "configured": True,
                "verification_status": "pending",
                "last_tested_at": None,
                "tested_configuration_hash": None,
            },
        )
    )

    assert "提醒" in content
    assert "telegram-alerts" in content
    assert "凭据状态" in content
    assert "可用" in content
    assert "待验证" in content
    assert "5705864725" not in content
    assert "****4725" in content
    assert "tested_configuration_hash" not in content
    assert "{" not in content


def test_notification_test_result_highlights_delivery_instead_of_health_json() -> None:
    content = renderable_plain_text(
        action_result_renderable(
            "notifications",
            "test",
            {
                "notification_id": "3177290b54c84912aea7b10b9dfabce",
                "publish_status": "accepted",
                "destination_id": "telegram-alerts",
                "health": {
                    "state": "healthy",
                    "queue_depth": 0,
                    "delivered_total": 1,
                    "destinations": {
                        "telegram-alerts": {
                            "sender": "telegram",
                            "delivered_total": 1,
                            "failed_total": 0,
                            "last_success_at": "2026-08-25T08:24:48+00:00",
                        }
                    },
                },
            },
            title="通知提醒 · telegram-alerts · 资源操作结果",
        )
    )

    assert "测试消息已送达" in content
    assert "telegram" in content
    assert "2026-08-25T08:24:48+00:00" in content
    assert "queue_depth" not in content
    assert "delivered_total" not in content
    assert "{" not in content


def test_market_data_failure_explains_result_and_next_action() -> None:
    content = renderable_plain_text(
        action_result_renderable(
            "data",
            "test",
            {
                "verification_status": "failed",
                "last_tested_at": "2026-08-25T08:42:16+00:00",
                "tested": [
                    "API authentication",
                    "AAPL Reference lookup",
                    "SPY hourly bar read",
                ],
                "not_tested": ["Options catalog and market data"],
                "capabilities_verified": [],
                "error_category": "invalid_response",
                "current_configuration_hash": "secret-noise",
            },
            title="市场数据 · massive · 资源操作结果",
        )
    )

    assert "连接验证失败" in content
    assert "Provider 返回了无法识别的响应" in content
    assert "已尝试检查" in content
    assert "未执行检查" in content
    assert "Massive API 兼容地址" in content
    assert "已验证能力" not in content
    assert "invalid_response" not in content
    assert "secret-noise" not in content
    assert "{" not in content


def test_market_data_failed_test_uses_failure_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = {
        "connection_id": "massive",
        "provider": "massive",
        "enabled": True,
        "verification_status": "pending",
    }
    monkeypatch.setattr(resources, "list_records", lambda state, kind: (record,))
    monkeypatch.setattr(
        resources,
        "execute_action",
        lambda state, kind, selected, action, **kwargs: {
            "verification_status": "failed",
            "tested": ["API authentication"],
            "not_tested": ["Options catalog and market data"],
            "capabilities_verified": [],
            "error_category": "invalid_response",
        },
    )

    async def run() -> tuple[ActivityOutcome, str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(120, 32)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("4", "2"):
                screen.submit(value)
                await pilot.pause(0.1)
            screen.submit("1")
            screen.submit("1")
            screen.submit("/confirm")
            await pilot.pause(0.1)
            output = screen.query_one("#command-output", RichLog)
            activity = output.activities[-1]
            status = str(screen.query_one("#command-status", Static).render())
            return activity.outcome, _log_text(output), status

    outcome, output, status = asyncio.run(run())
    assert outcome is ActivityOutcome.FAILURE
    assert "✗" in output
    assert "连接验证失败" in output
    assert status == "连接验证失败 · 请检查结果"


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
        "models": "模型连接",
        "notifications": "通知提醒",
    }
    monkeypatch.setattr(
        resources,
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
        resources,
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
    for label in ("交易账户", "市场数据", "模型连接", "通知提醒"):
        assert label in output


def test_empty_resource_groups_offer_new_configuration_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resources,
        "list_records",
        lambda state, kind: (),
    )
    labels = ("交易账户", "市场数据", "模型连接", "通知提醒")

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
        resources,
        "list_records",
        lambda state, kind: (record,),
    )
    monkeypatch.setattr(
        account,
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
        account_actions.AccountCliApplication,
        "run",
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
        resources,
        "list_records",
        lambda state, kind: (record,),
    )

    def execute(state: object, prompt: object) -> dict[str, object]:
        action = getattr(prompt, "action")
        values = dict(getattr(prompt, "values"))
        calls.append((action, values))
        return {"action": action, "orders": []}

    monkeypatch.setattr(account, "execute_order", execute)

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
        resources,
        "list_records",
        lambda state, kind: (record,),
    )
    monkeypatch.setattr(
        resources,
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
            screen.submit("6")
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


def test_deleting_model_connection_returns_to_model_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        {
            "connection_id": "primary-model",
            "provider": "openai",
            "enabled": True,
            "models": ["gpt-test"],
            "verification_status": "verified",
        }
    ]
    monkeypatch.setattr(
        resources,
        "list_records",
        lambda state, kind: tuple(records),
    )

    def execute(
        state: object,
        kind: str,
        selected: object,
        action: str,
        **kwargs: object,
    ) -> dict[str, str]:
        assert kind == "models"
        assert action == "delete"
        records.clear()
        return {"connection_id": "primary-model", "status": "deleted"}

    monkeypatch.setattr(resources, "execute_action", execute)

    async def run() -> tuple[tuple[str, ...], tuple[str, ...]]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("4", "3", "1", "7", "/confirm"):
                screen.submit(value)
                await pilot.pause(0.1)
            interaction = screen.session.interaction
            assert isinstance(interaction, ChoiceInteraction)
            return (
                screen.session.context,
                tuple(action.label for action in interaction.actions),
            )

    context, actions = asyncio.run(run())
    assert context == ("resources", "models")
    assert actions == ("添加模型连接",)


def test_notification_detail_only_offers_destination_owned_actions() -> None:
    actions = detail_actions("notifications")

    assert [action.id for action in actions] == [
        "test",
        "edit",
        "toggle",
        "advanced",
        "delete",
    ]
    assert "Launch 引用" in actions[3].description


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
        resources,
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
            screen.submit("2")
            await pilot.pause()
            return (
                str(screen.query_one("#command-context", Static).render()),
                screen.query_one("#command-input", WorkbenchCommandInput).placeholder,
                isinstance(screen.session.resources.wizard, ResourceWizardState),
            )

    context, placeholder, has_wizard = asyncio.run(run())
    assert context == "首页 / 运行准备 / 配置向导 · 通知提醒 · ops-alerts  ›"
    assert placeholder == "输入编号或命令；Enter 提交"
    assert has_wizard


def test_notification_wizard_uses_numbered_choices_and_automatic_identity() -> None:
    wizard = ResourceWizardState("notifications")

    name, label, detail, secret = wizard.next_prompt() or ("", "", "", False)

    assert name == "notification-provider"
    assert label == "请选择通知渠道（输入 1 或 2）"
    assert "通过选项选择通知渠道" in detail
    assert not secret

    wizard.accept(name, "2")
    wizard.generated_id = "telegram-alerts"

    assert wizard.answers == {"notification-provider": "telegram"}
    assert wizard.next_prompt() == (
        "secret-primary",
        "Telegram Bot Token",
        "从 BotFather 获取；内容会安全保存，不会显示或进入命令历史。",
        True,
    )
    assert "resource-id" not in wizard._steps()


def test_live_account_wizard_defaults_to_named_readonly_exchange_account() -> None:
    wizard = ResourceWizardState("accounts")

    wizard.accept("account-mode", "2")
    wizard.accept("account-provider", "okex")

    assert wizard.answers == {
        "account-mode": "live",
        "account-provider": "okx",
    }
    assert wizard._steps() == (
        "account-mode",
        "account-provider",
        "resource-id",
        "account-segment",
        "secret-primary",
        "secret-secondary",
        "secret-tertiary",
    )
    assert wizard._default("resource-id") == "okx-readonly"


def test_account_wizard_exposes_binance_and_okx_readonly_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(resources, "list_records", lambda state, kind: ())

    async def run() -> tuple[list[str], list[str], str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(120, 34)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("4", "1", "/new"):
                screen.submit(value)
                await pilot.pause(0.1)
            mode_prompts = [
                str(option.prompt)
                for option in screen.query_one("#guided-actions", ActionList)._options
            ]
            screen.submit("2")
            await pilot.pause()
            provider_prompts = [
                str(option.prompt)
                for option in screen.query_one("#guided-actions", ActionList)._options
            ]
            screen.submit("1")
            await pilot.pause()
            interaction = screen.session.interaction
            assert isinstance(interaction, InputInteraction)
            return (
                mode_prompts,
                provider_prompts,
                renderable_plain_text(interaction.value_summary),
                interaction.prompt,
            )

    modes, providers, summary, prompt = asyncio.run(run())
    assert modes == [
        "[1]  模拟账户  ·  使用本地余额，不连接交易所",
        "[2]  交易所账户  ·  连接 Binance 或 OKX 的真实账户",
    ]
    assert providers == [
        "[1]  Binance  ·  连接 Binance API",
        "[2]  OKX（原 OKEx）  ·  连接 OKX API",
    ]
    assert "Binance" in summary
    assert "只读（不下单、不转账）" in summary
    assert "binance-readonly" in summary
    assert prompt == "资源名称"


def test_notification_provider_uses_standard_action_cards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(resources, "list_records", lambda state, kind: ())

    async def run() -> tuple[list[str], str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("4", "4", "/new"):
                screen.submit(value)
                await pilot.pause(0.1)
            interaction = screen.session.interaction
            assert isinstance(interaction, ChoiceInteraction)
            prompts = [
                str(option.prompt)
                for option in screen.query_one("#guided-actions", ActionList)._options
            ]
            summary = renderable_plain_text(interaction.summary)
            screen.submit("telegram")
            await pilot.pause()
            return (
                prompts,
                summary,
                screen.query_one("#command-input", WorkbenchCommandInput).placeholder,
            )

    prompts, summary, placeholder = asyncio.run(run())
    assert prompts == [
        "[1]  飞书（推荐）  ·  使用群机器人 Webhook",
        "[2]  Telegram  ·  使用机器人令牌和 Chat ID",
    ]
    assert "创建通知提醒" in summary
    assert "<redacted>" not in "".join(prompts)
    assert placeholder == "Telegram Bot Token"


def test_notification_wizard_back_returns_to_previous_step() -> None:
    wizard = ResourceWizardState(
        "notifications",
        answers={"notification-provider": "telegram", "secret-primary": "token"},
        generated_id="telegram-alerts",
    )

    assert wizard.next_prompt()[0] == "chat-id"  # type: ignore[index]
    assert wizard.go_back()
    assert wizard.next_prompt()[0] == "secret-primary"  # type: ignore[index]
    assert wizard.go_back()
    assert wizard.next_prompt()[0] == "notification-provider"  # type: ignore[index]
    assert wizard.generated_id is None


def test_notification_wizard_selects_unique_automatic_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resources,
        "list_records",
        lambda state, kind: (
            {"destination_id": "feishu-alerts"},
            {"destination_id": "feishu-alerts-2"},
        ),
    )

    async def run() -> tuple[str, str, dict[str, Any], str | None]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            screen.submit("4")
            screen.submit("4")
            await pilot.pause(0.1)
            screen.submit("/new")
            await pilot.pause(0.1)
            screen.submit("1")
            await pilot.pause()
            wizard = screen.session.resources.wizard
            assert isinstance(wizard, ResourceWizardState)
            interaction = screen.session.interaction
            assert isinstance(interaction, InputInteraction)
            return (
                screen.query_one("#command-input", WorkbenchCommandInput).placeholder,
                renderable_plain_text(interaction.value_summary),
                wizard.redacted_summary(),
                wizard.generated_id,
            )

    placeholder, content, summary, generated_id = asyncio.run(run())
    assert placeholder == "飞书机器人 Webhook 地址"
    assert "创建通知提醒" in content
    assert "系统自动生成" in content
    assert summary["notification-provider"] == "feishu"
    assert generated_id == "feishu-alerts-3"


def test_notification_wizard_back_preserves_flow_and_cancel_discards_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(resources, "list_records", lambda state, kind: ())

    async def run() -> tuple[str, str, object, tuple[str, ...]]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("4", "4", "/new", "2"):
                screen.submit(value)
                await pilot.pause(0.1)
            screen.submit("/back")
            await pilot.pause()
            back_placeholder = screen.query_one(
                "#command-input", WorkbenchCommandInput
            ).placeholder
            wizard = screen.session.resources.wizard
            assert isinstance(wizard, ResourceWizardState)
            assert wizard.answers == {}
            screen.submit("2")
            screen.submit("/cancel")
            await pilot.pause()
            return (
                back_placeholder,
                screen.query_one("#command-input", WorkbenchCommandInput).placeholder,
                screen.session.resources.wizard,
                screen.session.context,
            )

    back_placeholder, cancelled_placeholder, wizard, context = asyncio.run(run())
    assert back_placeholder == "输入编号或命令；Enter 提交"
    assert cancelled_placeholder == "输入编号或命令；Enter 提交"
    assert wizard is None
    assert context == ("resources", "notifications")


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


def test_model_wizard_uses_provider_specific_product_paths() -> None:
    hosted = ResourceWizardState("models")
    hosted.accept("model-provider", "openai")
    assert hosted._steps() == (
        "model-provider",
        "resource-id",
        "endpoint",
        "secret-primary",
    )
    assert hosted._default("endpoint") == "https://api.openai.com/v1"

    local = ResourceWizardState("models")
    local.accept("model-provider", "ollama")
    assert local._steps() == ("model-provider", "resource-id", "endpoint")
    assert local._default("endpoint") == "http://127.0.0.1:11434/v1"

    custom = ResourceWizardState("models")
    custom.accept("model-provider", "custom")
    assert custom._steps() == (
        "model-provider",
        "resource-id",
        "model-mode",
        "endpoint",
        "secret-primary",
    )


def test_model_wizard_requires_new_hosted_credential_but_reuses_existing() -> None:
    created = ResourceWizardState(
        "models",
        answers={
            "model-provider": "openai",
            "resource-id": "openai-main",
        },
    )
    with pytest.raises(ValueError, match="OpenAI 需要 API Key"):
        created.accept("secret-primary", "")

    edited = ResourceWizardState(
        "models",
        {
            "connection_id": "openai-main",
            "provider": "openai",
            "credential_id": "openai-main-auth",
        },
        answers={"model-provider": "openai"},
    )
    edited.accept("secret-primary", "")
    assert edited.answers["secret-primary"] == ""

    edited.answers["model-provider"] = "anthropic"
    with pytest.raises(ValueError, match="Anthropic 需要 API Key"):
        edited.accept("secret-primary", "")


def test_model_provider_is_a_numbered_choice_with_product_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(resources, "list_records", lambda state, kind: ())

    async def run() -> tuple[list[str], str, str, str]:
        app = KairosWorkbenchApp(_state())
        async with app.run_test(size=(120, 36)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("4", "3", "/new"):
                screen.submit(value)
                await pilot.pause(0.1)
            interaction = screen.session.interaction
            assert isinstance(interaction, ChoiceInteraction)
            prompts = [
                str(option.prompt)
                for option in screen.query_one("#guided-actions", ActionList)._options
            ]
            summary = renderable_plain_text(interaction.summary)
            screen.submit("4")
            await pilot.pause()
            next_interaction = screen.session.interaction
            assert isinstance(next_interaction, InputInteraction)
            return (
                prompts,
                summary,
                renderable_plain_text(next_interaction.value_summary),
                screen.query_one("#command-input", WorkbenchCommandInput).placeholder,
            )

    prompts, summary, next_summary, placeholder = asyncio.run(run())
    assert len(prompts) == 6
    assert prompts[0] == "[1]  OpenAI（推荐）  ·  使用 Responses API"
    assert prompts[3] == "[4]  Ollama（本地）  ·  连接 Ollama，默认无需 API Key"
    assert "创建模型连接" in summary
    assert "'kind'" not in summary
    assert "Ollama" in next_summary
    assert "http://127.0.0.1:11434/v1" in next_summary
    assert "无需 API Key" in next_summary
    assert placeholder == "资源名称"


def test_model_connection_wizard_discovers_tests_and_atomically_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="model-workbench"
    )
    state = WorkbenchState(owner=workspace, workspace_arg=workspace.paths.root)
    monkeypatch.setattr(
        ModelProviderConnectionApplication,
        "discover",
        lambda self, connection, *, secret, probe=None: (
            {"id": "qwen3:8b", "name": "Qwen 3 8B", "source": "test"},
            {"id": "deepseek-r1:8b", "name": "DeepSeek R1 8B", "source": "test"},
        ),
    )
    monkeypatch.setattr(
        ModelProviderConnectionApplication,
        "probe",
        lambda self, connection, model, *, secret, probe=None: {
            "succeeded": True,
            "detail": "最小文本响应成功",
            "error_category": None,
        },
    )

    async def run() -> tuple[str, str, object, tuple[str, ...]]:
        app = KairosWorkbenchApp(state)
        async with app.run_test(size=(120, 36)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("4", "3"):
                screen.submit(value)
                await pilot.pause(0.1)
            for value in ("/new", "4", "ollama-local", ""):
                screen.submit(value)
                await pilot.pause(0.1)
            interaction = screen.session.interaction
            assert isinstance(interaction, ChoiceInteraction)
            assert "选择验证模型" in interaction.title
            assert len(interaction.actions) == 3
            screen.submit("1")
            await pilot.pause(0.1)
            screen.submit("/confirm")
            await pilot.pause(0.1)
            list_interaction = screen.session.interaction
            assert isinstance(list_interaction, ChoiceInteraction)
            assert screen.session.context == ("resources", "models")
            assert [action.label for action in list_interaction.actions] == [
                "ollama-local",
                "添加模型连接",
            ]
            screen.submit("1")
            await pilot.pause(0.1)
            detail_interaction = screen.session.interaction
            assert isinstance(detail_interaction, ChoiceInteraction)
            return (
                str(screen.query_one("#command-context", Static).render()),
                _log_text(screen.query_one("#command-output", RichLog)),
                screen.session.resources.wizard,
                tuple(action.label for action in detail_interaction.actions),
            )

    context, output, wizard, actions = asyncio.run(run())
    connection = ModelProviderConnectionApplication(workspace).show("ollama-local")
    assert "模型连接" in context
    assert "ollama-local" in context
    assert wizard is None
    assert connection["models"] == ["qwen3:8b", "deepseek-r1:8b"]
    assert connection["verification_status"] == "verified"
    assert connection["verified_models"] == ["qwen3:8b"]
    assert "发现 2 个模型" in output
    assert "最小文本响应成功" in output
    assert "修改配置" in actions
    assert "删除连接" in actions


def test_model_catalog_failure_allows_manual_model_and_saves_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="model-manual"
    )
    state = WorkbenchState(owner=workspace, workspace_arg=workspace.paths.root)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise PermissionError("HTTP Error 403: Forbidden")

    monkeypatch.setattr(ModelProviderConnectionApplication, "discover", forbidden)
    monkeypatch.setattr(
        ModelProviderConnectionApplication,
        "probe",
        lambda self, connection, model, *, secret, probe=None: {
            "succeeded": True,
            "detail": "最小文本响应成功",
            "error_category": None,
        },
    )

    async def run() -> tuple[str, tuple[str, ...]]:
        app = KairosWorkbenchApp(state)
        async with app.run_test(size=(120, 36)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("4", "3", "/new", "4", "ikun", ""):
                screen.submit(value)
                await pilot.pause(0.1)
            interaction = screen.session.interaction
            assert isinstance(interaction, ChoiceInteraction)
            labels = tuple(action.label for action in interaction.actions)
            screen.submit("1")
            await pilot.pause()
            screen.submit("gpt-5.6-sol")
            await pilot.pause(0.1)
            screen.submit("/confirm")
            await pilot.pause(0.1)
            return _log_text(screen.query_one("#command-output", RichLog)), labels

    output, labels = asyncio.run(run())
    saved = ModelProviderConnectionApplication(workspace).show("ikun")
    assert labels == ("手动输入模型 ID",)
    assert "模型目录不可用" in output
    assert saved["models"] == ["gpt-5.6-sol"]
    assert saved["verification_status"] == "verified"


def test_saved_model_can_send_message_and_show_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="model-conversation"
    )
    ModelProviderConnectionApplication(workspace).configure(
        "ollama-local", provider="ollama", models=("qwen3:8b",)
    )
    state = WorkbenchState(owner=workspace, workspace_arg=workspace.paths.root)
    monkeypatch.setattr(
        "kairospy.strategy.apps.agent.application.AgentResourceApplication.converse_with_model",
        lambda self, connection_id, model, message: {
            "succeeded": True,
            "verification_status": "verified",
            "model": model,
            "message": message,
            "response": "你好，我是 Qwen。",
        },
    )

    async def run() -> tuple[str, str]:
        app = KairosWorkbenchApp(state)
        async with app.run_test(size=(120, 36)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("4", "3", "1", "1", "1"):
                screen.submit(value)
                await pilot.pause(0.1)
            interaction = screen.session.interaction
            assert isinstance(interaction, InputInteraction)
            screen.submit("你好，请介绍自己")
            await pilot.pause()
            screen.submit("/confirm")
            await pilot.pause(0.1)
            return (
                _log_text(screen.query_one("#command-output", RichLog)),
                screen.query_one("#command-input", WorkbenchCommandInput).placeholder,
            )

    output, placeholder = asyncio.run(run())
    assert "你好，请介绍自己" in output
    assert "你好，我是 Qwen。" in output
    assert placeholder == "输入编号或命令；Enter 提交"


def test_model_connection_home_discards_staged_hosted_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="model-cancel"
    )
    state = WorkbenchState(owner=workspace, workspace_arg=workspace.paths.root)
    monkeypatch.setattr(
        ModelProviderConnectionApplication,
        "discover",
        lambda self, connection, *, secret, probe=None: ({"id": "gpt-test"},),
    )

    async def run() -> tuple[ResourceWizardState, tuple[str, ...]]:
        app = KairosWorkbenchApp(state)
        async with app.run_test(size=(120, 36)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("4", "3"):
                screen.submit(value)
                await pilot.pause(0.1)
            for value in (
                "/new",
                "1",
                "openai-main",
                "",
                "secret-never-persist",
            ):
                screen.submit(value)
                await pilot.pause(0.1)
            wizard = screen.session.resources.wizard
            assert isinstance(wizard, ResourceWizardState)
            assert wizard.model_draft is not None
            assert not (
                workspace.paths.credentials_root() / "openai-main-openai-auth.toml"
            ).exists()
            screen.submit("/home")
            await pilot.pause()
            return wizard, screen.session.context

    wizard, context = asyncio.run(run())
    assert context == ()
    assert wizard.model_draft is None
    assert wizard.answers["secret-primary"] == ""
    assert not (
        workspace.paths.credentials_root() / "openai-main-openai-auth.toml"
    ).exists()
    assert not (workspace.paths.model_connections_root() / "openai-main.toml").exists()


def test_editing_market_data_preserves_its_credential_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_credentials: list[str] = []
    configured_connections: list[str] = []
    market_bindings: list[tuple[str, str]] = []

    class Credentials:
        def __init__(self, owner: object) -> None:
            pass

        def configure(self, credential_id: str, **kwargs: object) -> None:
            configured_credentials.append(credential_id)

    class Reference:
        def __init__(self, owner: object) -> None:
            pass

        def configure_massive(
            self, *, credential_id: str, **kwargs: object
        ) -> dict[str, str]:
            configured_connections.append(credential_id)
            return {"connection_id": "massive", "credential_id": credential_id}

    class MarketBindings:
        def __init__(self, owner: object) -> None:
            pass

        def bind_connection(self, connection_id: str, *, product: str) -> None:
            market_bindings.append((connection_id, product))

    monkeypatch.setattr(
        resource_wizard, "CredentialConfigurationApplication", Credentials
    )
    monkeypatch.setattr(
        resource_wizard, "ReferenceProviderConfigurationApplication", Reference
    )
    monkeypatch.setattr(
        resource_wizard, "MarketProviderBindingApplication", MarketBindings
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
    assert market_bindings == [("massive", "equity")]


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
        resources,
        "list_records",
        lambda state, kind: (record,),
    )

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry-run must not execute the resource deletion")

    monkeypatch.setattr(
        resources,
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
            screen.submit("7")
            await pilot.pause(0.1)
            return (
                _log_text(screen.query_one("#command-output", RichLog)),
                screen.session.visible_records,
            )

    output, records = asyncio.run(run())
    assert "预览" in output
    assert tuple(record.key for record in records) == ("paper-main",)


def test_resource_setup_uses_masked_single_input_and_never_records_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[str] = []
    monkeypatch.setattr(
        resources,
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
        resources,
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
            for value in ("/new", "1", "massive-main", "1", "", "2"):
                screen.submit(value)
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
        resources,
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
            for value in ("/new", "1", "massive-main", "1", "", "2"):
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
