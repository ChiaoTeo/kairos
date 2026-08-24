"""Workspace runtime resource screens."""

from __future__ import annotations

from typing import Any

from rich.pretty import Pretty
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Label, OptionList, RichLog
from textual.worker import Worker

from kairospy.investment.apps.account.application import AccountConfigurationApplication
from kairospy.strategy.apps.agent.application import AgentResourceApplication
from kairospy.system.apps.configuration.application import (
    ConfigurationReferenceApplication,
    WorkspaceResourceLifecycleApplication,
)
from kairospy.system.apps.launch.application import (
    LaunchNotificationConfigurationApplication,
)
from kairospy.strategy.apps.notification.application import NotificationAdminApplication
from kairospy.investment.apps.reference.application import ReferenceProviderConfigurationApplication

from ..dialogs import ConfirmDialog, InputDialog
from ..widgets import ActionItem, ActionList, WorkspaceHeader
from .resource_setup import ResourceSetupScreen
from .account import AccountOperationsScreen


RESOURCE_ACTIONS = (
    ActionItem("accounts", "交易账户", "账户身份、权限与连接验证", "1"),
    ActionItem("data", "市场数据", "Reference 与行情数据连接", "2"),
    ActionItem("models", "AI 模型", "模型服务、凭据与可用模型", "3"),
    ActionItem("notifications", "通知提醒", "飞书、Telegram 等通知目标", "4"),
    ActionItem("check", "检查所有连接", "汇总未配置、待验证与失败原因", "5"),
)


class ResourcesScreen(Screen[None]):
    TITLE = "Kairos"
    SUB_TITLE = "首页 › 运行资源"
    BINDINGS = [Binding("escape", "back", "返回"), Binding("r", "refresh", "刷新")]

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label("管理运行资源", id="page-title")
        yield Label("正在检查已配置连接…", id="resource-summary")
        yield ActionList(*RESOURCE_ACTIONS, id="resource-actions")
        yield RichLog(id="resource-readiness", wrap=True, highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        self.action_refresh()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self.run_worker(
            self._summary,
            name="resource-summary",
            group="resource-summary",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _summary(self) -> dict[str, tuple[int, int]]:
        owner = self.app.state.owner  # type: ignore[attr-defined]
        if owner is None:
            raise RuntimeError(self.app.state.load_error)  # type: ignore[attr-defined]
        groups = {
            "accounts": AccountConfigurationApplication(owner).list(),
            "data": ReferenceProviderConfigurationApplication(owner).list(),
            "models": list(AgentResourceApplication(owner).model_connections()),
            "notifications": NotificationAdminApplication(owner).list(),
        }
        return {
            key: (
                len(records),
                sum(
                    record.get("verification_status") == "verified"
                    for record in records
                ),
            )
            for key, records in groups.items()
        }

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "resource-summary":
            return
        label = self.query_one("#resource-summary", Label)
        if event.state.name == "ERROR":
            label.update(f"检查失败：{event.worker.error}")
        elif event.state.name == "SUCCESS":
            values = event.worker.result or {}
            total = sum(count for count, _ in values.values())
            verified = sum(ok for _, ok in values.values())
            label.update(
                f"已配置 {total} 个连接 · {verified} 个已验证 · {total - verified} 个需要处理"
            )
            self._render_readiness(values)

    def _render_readiness(self, values: dict[str, tuple[int, int]]) -> None:
        log = self.query_one("#resource-readiness", RichLog)
        log.clear()
        labels = {
            "accounts": "交易账户",
            "data": "市场数据",
            "models": "AI 模型",
            "notifications": "通知提醒",
        }
        for key, (count, verified) in values.items():
            state = (
                "尚未配置"
                if not count
                else (
                    "可用"
                    if count == verified
                    else f"{count - verified} 个需要验证或修复"
                )
            )
            log.write(f"{labels[key]}  {verified}/{count} 已验证  ·  {state}")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        kind = event.option.id
        if kind in {"accounts", "data", "models", "notifications"}:
            self.app.push_screen(ResourceListScreen(kind))
        elif kind == "check":
            self.action_refresh()


class ResourceListScreen(Screen[None]):
    TITLE = "Kairos"
    BINDINGS = [
        Binding("escape", "back", "返回"),
        Binding("r", "refresh", "刷新"),
        Binding("n", "new", "添加"),
    ]

    def __init__(self, kind: str) -> None:
        super().__init__()
        self.kind = kind
        self._records: dict[str, dict[str, Any]] = {}
        self.sub_title = f"首页 › 运行资源 › {_kind_label(kind)}"

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label(_kind_label(self.kind), id="page-title")
        yield Label("正在读取…", id="resource-list-status")
        yield DataTable(id="resource-table", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#resource-table", DataTable).add_columns(
            "名称", "提供方", "启用", "验证", "问题"
        )
        self.action_refresh()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self.run_worker(
            self._load,
            name="resource-list",
            group="resource-list",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def action_new(self) -> None:
        self.app.push_screen(ResourceSetupScreen(self.kind), self._saved)

    def _saved(self, result: dict[str, Any] | None) -> None:
        if result is not None:
            self.notify(f"{_kind_label(self.kind)}已保存；请完成真实连接测试。")
            self.action_refresh()

    def _load(self) -> tuple[dict[str, Any], ...]:
        owner = self.app.state.owner  # type: ignore[attr-defined]
        if self.kind == "accounts":
            return tuple(AccountConfigurationApplication(owner).list())
        if self.kind == "data":
            return tuple(ReferenceProviderConfigurationApplication(owner).list())
        if self.kind == "models":
            return tuple(
                dict(value)
                for value in AgentResourceApplication(owner).model_connections()
            )
        return tuple(
            dict(value) for value in NotificationAdminApplication(owner).list()
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "resource-list":
            return
        status = self.query_one("#resource-list-status", Label)
        if event.state.name == "ERROR":
            status.update(f"读取失败：{event.worker.error}")
            return
        if event.state.name != "SUCCESS":
            return
        records = tuple(event.worker.result or ())
        table = self.query_one("#resource-table", DataTable)
        table.clear()
        self._records.clear()
        for record in records:
            identity = _resource_id(self.kind, record)
            self._records[identity] = record
            issues = record.get("issues") or ()
            table.add_row(
                identity,
                str(
                    record.get("provider")
                    or record.get("broker")
                    or record.get("sender")
                    or "—"
                ),
                "是" if record.get("enabled", True) else "否",
                str(record.get("verification_status") or "pending"),
                "；".join(map(str, issues)) or "—",
                key=identity,
            )
        status.update("尚未配置。" if not records else f"共 {len(records)} 个连接")
        if records:
            table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        record = self._records.get(str(event.row_key.value))
        if record is not None:
            self.app.push_screen(ResourceDetailScreen(self.kind, record))


class ResourceDetailScreen(Screen[None]):
    TITLE = "Kairos"
    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self, kind: str, record: dict[str, Any]) -> None:
        super().__init__()
        self.kind = kind
        self.record = record
        self.identity = _resource_id(kind, record)
        self._pending_action = ""
        self._pending_launch = ""
        self.sub_title = f"首页 › 运行资源 › {_kind_label(kind)} › {self.identity}"

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label(self.identity, id="page-title")
        actions = self._actions()
        yield ActionList(*actions, id="resource-detail-actions")
        yield Label("选择操作。", id="resource-detail-status")
        yield RichLog(id="resource-detail-result", wrap=True, highlight=False)
        yield Footer()

    def _actions(self) -> tuple[ActionItem, ...]:
        if self.kind == "models":
            return (
                ActionItem(
                    "test", "测试模型", "执行最小文本调用；云端可能产生少量费用", "1"
                ),
                ActionItem("models", "查看已保存模型", "显示当前连接的模型 ID", "2"),
                ActionItem("edit", "修改配置", "打开安全配置表单", "3"),
                ActionItem(
                    "advanced", "安全与高级信息", "查看版本、测试范围和引用", "4"
                ),
                ActionItem("toggle", "启用或停用", "切换连接可用状态", "5"),
                ActionItem("delete", "删除连接", "移除配置和验证记录", "6"),
            )
        if self.kind == "notifications":
            return (
                ActionItem(
                    "test", "发送真实测试消息", "验证认证和真实 provider delivery", "1"
                ),
                ActionItem("attach", "绑定到 Launch", "添加通知 route", "2"),
                ActionItem("detach", "从 Launch 解绑", "移除所有相关 route", "3"),
                ActionItem("validate", "校验通知配置", "检查指定运行模式", "4"),
                ActionItem(
                    "advanced", "安全与高级信息", "查看版本、测试范围和引用", "5"
                ),
                ActionItem("toggle", "启用或停用", "切换通知提醒状态", "6"),
                ActionItem("delete", "删除提醒", "移除配置和验证记录", "7"),
            )
        if self.kind == "accounts":
            return (
                ActionItem("operations", "账户运行查询", "余额、持仓、费率与订单", "1"),
                ActionItem("test", "测试连接", "执行真实、安全的只读连接验证", "2"),
                ActionItem("edit", "修改配置", "打开安全配置表单", "3"),
                ActionItem(
                    "advanced", "安全与高级信息", "查看版本、测试范围和引用", "4"
                ),
                ActionItem("toggle", "启用或停用", "切换连接可用状态", "5"),
                ActionItem("delete", "删除连接", "移除配置和验证记录", "6"),
            )
        return (
            ActionItem("test", "测试连接", "执行真实、安全的只读连接验证", "1"),
            ActionItem("edit", "修改配置", "打开安全配置表单", "2"),
            ActionItem("advanced", "安全与高级信息", "查看版本、测试范围和引用", "3"),
            ActionItem("toggle", "启用或停用", "切换连接可用状态", "4"),
            ActionItem("delete", "删除连接", "移除配置和验证记录", "5"),
        )

    def on_mount(self) -> None:
        self._show(self.record)

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        action = event.option.id
        if action == "operations" and self.kind == "accounts":
            self.app.push_screen(AccountOperationsScreen(self.record))
        elif action == "test" and self.kind == "models":
            self.app.push_screen(
                InputDialog("输入要测试的模型", placeholder="例如 gpt-5"),
                lambda value: self._confirm_test(value) if value else None,
            )
        elif action == "test" and self.kind == "notifications":
            self._confirm_test(None)
        elif action == "test":
            self._confirm_test(None)
        elif action in {"attach", "detach"} and self.kind == "notifications":
            self._pending_action = action
            self.app.push_screen(InputDialog("Launch ID"), self._launch_selected)
        elif action == "validate" and self.kind == "notifications":
            self.app.push_screen(
                InputDialog("运行模式", value="paper"),
                lambda value: self._run("validate", value) if value else None,
            )
        elif action == "models":
            self._show({"models": list(self.record.get("models") or ())})
        elif action == "edit":
            self.app.push_screen(
                ResourceSetupScreen(self.kind, self.record), self._edited
            )
        elif action == "advanced":
            self._run("advanced")
        elif action in {"toggle", "delete"}:
            if self.app.state.yes:  # type: ignore[attr-defined]
                self._run(action)
                return
            self.app.push_screen(
                ConfirmDialog("确认资源变更", f"确认对 {self.identity} 执行{action}？"),
                lambda confirmed: self._run(action) if confirmed else None,
            )
        elif action is not None:
            self._run(action)

    def _confirm_test(self, value: str | None) -> None:
        state = self.app.state  # type: ignore[attr-defined]
        if state.yes:
            self._run("test", value)
            return
        detail = {
            "accounts": "将连接真实账户服务并执行安全的只读认证与权限检查。",
            "data": "将连接真实行情服务并读取固定的低成本样本。",
            "models": "将向模型服务发送最小请求，云端服务可能产生少量费用。",
            "notifications": "将向真实外部渠道发送一条测试消息。",
        }[self.kind]
        self.app.push_screen(
            ConfirmDialog("测试运行资源", detail, confirm_label="开始测试"),
            lambda confirmed: self._run("test", value) if confirmed else None,
        )

    def _launch_selected(self, value: str | None) -> None:
        if not value:
            return
        self._pending_launch = value
        if self._pending_action == "attach":
            self.app.push_screen(
                InputDialog("通知 Route", value="signals"), self._route_selected
            )
            return
        self._confirm_notification_route("detach", "从 Launch 移除这个通知目标？")

    def _route_selected(self, value: str | None) -> None:
        if value:
            self._confirm_notification_route(
                "attach", f"将 {self.identity} 绑定到 {self._pending_launch}/{value}？", value
            )

    def _confirm_notification_route(
        self, action: str, message: str, route: str | None = None
    ) -> None:
        state = self.app.state  # type: ignore[attr-defined]
        if state.yes:
            self._run(action, route)
            return
        self.app.push_screen(
            ConfirmDialog("通知 Launch 绑定", message, confirm_label="继续"),
            lambda confirmed: self._run(action, route) if confirmed else None,
        )

    def _edited(self, result: dict[str, Any] | None) -> None:
        if result is not None:
            self.record = result
            self.query_one("#resource-detail-status", Label).update(
                "配置已保存；变更后需要重新测试。"
            )
            self._show(result)

    def _run(self, action: str, value: str | None = None) -> None:
        state = self.app.state  # type: ignore[attr-defined]
        if action in {"test", "toggle", "delete", "attach", "detach"} and (
            state.dry_run or state.no_exec
        ):
            self._show(
                {"status": "preview", "action": action, "resource": self.identity}
            )
            return
        self.query_one("#resource-detail-status", Label).update(f"正在执行：{action}…")
        self.run_worker(
            lambda: self._execute(action, value),
            name="resource-action",
            group="resource-action",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _execute(self, action: str, value: str | None) -> Any:
        owner = self.app.state.owner  # type: ignore[attr-defined]
        if action == "delete":
            return WorkspaceResourceLifecycleApplication(owner).delete(
                _reference_kind(self.kind), self.identity
            )
        if self.kind == "accounts":
            app = AccountConfigurationApplication(owner)
            if action == "test":
                return app.test_connection(self.identity)
            if action == "refresh":
                return app.show(self.identity)
            if action == "toggle":
                disabled = str(self.record.get("status") or "").lower() == "disabled"
                return app.modify(
                    self.identity, status="configured" if disabled else "disabled"
                )
            if action == "advanced":
                return _advanced_resource(owner, self.kind, self.identity, self.record)
            raise ValueError(f"unsupported account action: {action}")
        if self.kind == "data":
            app = ReferenceProviderConfigurationApplication(owner)
            if action == "test":
                return app.test_connection(self.identity)
            if action == "toggle":
                return app.set_enabled(
                    self.identity,
                    enabled=not bool(self.record.get("enabled", True)),
                )
            if action == "advanced":
                return _advanced_resource(owner, self.kind, self.identity, self.record)
            return app.show(self.identity)
        if self.kind == "models":
            app = AgentResourceApplication(owner)
            if action == "test":
                return app.test_model_connection(self.identity, value or "")
            if action == "toggle":
                return app.set_model_connection_enabled(
                    self.identity, enabled=not bool(self.record.get("enabled", True))
                )
            if action == "advanced":
                return _advanced_resource(owner, self.kind, self.identity, self.record)
            return next(
                item
                for item in app.model_connections()
                if item["connection_id"] == self.identity
            )
        app = NotificationAdminApplication(owner)
        if action == "test":
            import asyncio

            return asyncio.run(
                NotificationAdminApplication(owner).test_destination(self.identity)
            )
        if action == "toggle":
            return app.set_enabled(
                self.identity, not bool(self.record.get("enabled", True))
            )
        if action == "advanced":
            return _advanced_resource(owner, self.kind, self.identity, self.record)
        if action == "validate":
            return NotificationAdminApplication(owner).validate_workspace(
                mode=value or "paper"
            )
        if action == "attach":
            app.show(self.identity)
            return LaunchNotificationConfigurationApplication(owner).attach(
                self._pending_launch,
                self.identity,
                route=value or "signals",
                default=True,
            )
        if action == "detach":
            return LaunchNotificationConfigurationApplication(owner).detach(
                self._pending_launch, self.identity
            )
        return app.show(self.identity)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "resource-action":
            return
        if event.state.name == "ERROR":
            self.query_one("#resource-detail-status", Label).update(
                f"操作失败：{event.worker.error}"
            )
        elif event.state.name == "SUCCESS":
            if isinstance(event.worker.result, dict):
                self.record = event.worker.result
            self.query_one("#resource-detail-status", Label).update("操作完成")
            self._show(event.worker.result)

    def _show(self, value: Any) -> None:
        log = self.query_one("#resource-detail-result", RichLog)
        log.clear()
        log.write(Pretty(value, expand_all=True))


def _kind_label(kind: str) -> str:
    return {
        "accounts": "交易账户",
        "data": "市场数据",
        "models": "AI 模型",
        "notifications": "通知提醒",
    }[kind]


def _resource_id(kind: str, record: dict[str, Any]) -> str:
    key = {
        "accounts": "account_id",
        "data": "connection_id",
        "models": "connection_id",
        "notifications": "destination_id",
    }[kind]
    return str(record.get(key) or "unknown")


def _reference_kind(kind: str) -> str:
    return {
        "accounts": "account",
        "data": "market_data",
        "models": "ai_model",
        "notifications": "notification",
    }[kind]


def _advanced_resource(
    owner: Any, kind: str, identity: str, record: dict[str, Any]
) -> dict[str, Any]:
    references = ConfigurationReferenceApplication(owner)
    if kind == "accounts":
        uses = references.account_references(identity)
    elif kind == "data":
        uses = references.data_provider_references(identity)
    elif kind == "notifications":
        uses = references.destination_references(identity)
    else:
        uses = references.model_connection_references(identity)
    return {
        "identity": identity,
        "credential_id": record.get("credential_id"),
        "current_configuration_hash": record.get("current_configuration_hash"),
        "tested_configuration_hash": record.get("tested_configuration_hash"),
        "tested": record.get("tested") or [],
        "not_tested": record.get("not_tested") or [],
        "references": uses,
    }
