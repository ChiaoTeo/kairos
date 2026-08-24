"""Risk, Capital, and provider capability tools in the shared workbench."""

from __future__ import annotations

from typing import Any

from rich.pretty import Pretty
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Label, OptionList, RichLog
from textual.worker import Worker

from kairospy.system.apps.components.application import (
    CapitalSystemClient,
    NativeCliApplication,
    RiskSystemClient,
)

from ..dialogs import InputDialog, SelectDialog, SelectOption
from ..widgets import ActionItem, ActionList, WorkspaceHeader


BUSINESS_TOOL_ACTIONS = (
    ActionItem("risk", "Risk 工具", "策略校验、预算、限额与预留", "1"),
    ActionItem("capital", "Capital 工具", "资金需求、计划与当前状态", "2"),
    ActionItem("integration", "Provider 集成能力", "Transfer 与 Earn 能力边界", "3"),
)


class BusinessToolsScreen(Screen[None]):
    TITLE = "Kairos"
    SUB_TITLE = "首页 › 系统维护 › 业务工具"
    BINDINGS = [Binding("escape", "back", "返回")]

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label("业务工具", id="page-title")
        yield ActionList(*BUSINESS_TOOL_ACTIONS, id="business-tool-actions")
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id == "risk":
            self.app.push_screen(RiskToolsScreen())
        elif event.option.id == "capital":
            self.app.push_screen(CapitalToolsScreen())
        elif event.option.id == "integration":
            self.app.push_screen(IntegrationCapabilitiesScreen())


RISK_ACTIONS = (
    ActionItem("schema", "查看 Schema", "查看 Risk 请求结构", "1"),
    ActionItem("doctor", "检查请求文件", "校验 policy 或 authorization 文件", "2"),
    ActionItem("preview", "本地预演", "用 policy 文件评估 Risk request", "3"),
    ActionItem("health", "运行状态", "连接 workspace Risk 服务", "4"),
    ActionItem("limits", "限额使用", "读取指定 Risk actor 的限额", "5"),
    ActionItem("reservations", "活动预留", "读取指定 Risk actor 的预留", "6"),
)


class RiskToolsScreen(Screen[None]):
    TITLE = "Kairos"
    SUB_TITLE = "首页 › 系统维护 › 业务工具 › Risk"
    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self) -> None:
        super().__init__()
        self._action = ""
        self._kind = ""
        self._first_path = ""

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label("Risk", id="page-title")
        yield ActionList(*RISK_ACTIONS, id="risk-tool-actions")
        yield Label("选择 Risk 操作。", id="risk-tool-status")
        yield RichLog(id="risk-tool-result", wrap=True, highlight=False)
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        action = event.option.id
        if action is None:
            return
        self._action = action
        if action == "schema":
            self.app.push_screen(
                SelectDialog(
                    "Schema 类型",
                    (
                        SelectOption("all", "全部"),
                        SelectOption("policy", "Policy"),
                        SelectOption("authorization", "Authorization"),
                    ),
                ),
                self._schema_kind,
            )
        elif action == "doctor":
            self.app.push_screen(
                SelectDialog(
                    "请求类型",
                    (
                        SelectOption("policy", "Policy"),
                        SelectOption("authorization", "Authorization"),
                    ),
                ),
                self._doctor_kind,
            )
        elif action == "preview":
            self.app.push_screen(
                InputDialog("Risk policy 文件", value="risk-policy.json"),
                self._preview_policy,
            )
        elif action in {"limits", "reservations"}:
            self.app.push_screen(
                InputDialog("Risk actor ID", value="risk"), self._actor_selected
            )
        else:
            self._run(action)

    def _schema_kind(self, value: str | None) -> None:
        if value is not None:
            self._run("schema", [] if value == "all" else [value])

    def _doctor_kind(self, value: str | None) -> None:
        if value is None:
            return
        self._kind = value
        self.app.push_screen(InputDialog("请求文件"), self._doctor_file)

    def _doctor_file(self, value: str | None) -> None:
        if value:
            self._run("doctor", ["--kind", self._kind, "--file", value])

    def _preview_policy(self, value: str | None) -> None:
        if not value:
            return
        self._first_path = value
        self.app.push_screen(
            InputDialog("Risk request 文件", value="risk-request.json"),
            self._preview_request,
        )

    def _preview_request(self, value: str | None) -> None:
        if value:
            self._run(
                "preview",
                ["--policy-file", self._first_path, "--request-file", value],
            )

    def _actor_selected(self, value: str | None) -> None:
        if value:
            self._run(self._action, [value])

    def _run(self, action: str, arguments: list[str] | None = None) -> None:
        self.query_one("#risk-tool-status", Label).update(
            f"正在执行：{action}；Ctrl+C 可取消等待…"
        )
        self.run_worker(
            lambda: self._execute(action, arguments or []),
            name=f"risk-tool-{action}",
            group="risk-tool",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _execute(self, action: str, arguments: list[str]) -> dict[str, Any]:
        owner = self.app.state.owner  # type: ignore[attr-defined]
        if owner is None:
            raise RuntimeError(self.app.state.load_error or "当前没有可用的 workspace")  # type: ignore[attr-defined]
        if action in {"schema", "doctor", "preview"}:
            return NativeCliApplication(owner).run(
                "risk", ["standalone", action, *arguments]
            )
        socket = owner.paths.process_socket("risk")
        if not socket.exists():
            raise RuntimeError("workspace Risk 服务尚未运行")
        client = RiskSystemClient(socket, view_root=owner.paths.snapshots, timeout=30.0)
        if action == "health":
            return client.health()
        actor_id = arguments[0]
        if action == "limits":
            return client.latest_limits(actor_id=actor_id)
        if action == "reservations":
            return client.latest_reservations(actor_id=actor_id)
        raise RuntimeError(f"unknown Risk action: {action}")

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "risk-tool":
            return
        status = self.query_one("#risk-tool-status", Label)
        if event.state.name == "ERROR":
            status.update(f"操作失败：{event.worker.error}")
        elif event.state.name == "SUCCESS":
            status.update("操作完成")
            log = self.query_one("#risk-tool-result", RichLog)
            log.clear()
            log.write(Pretty(event.worker.result, expand_all=True))


CAPITAL_ACTIONS = (
    ActionItem("schema", "查看 Schema", "查看 Capital 请求结构", "1"),
    ActionItem("doctor", "检查请求文件", "校验 Capital 请求", "2"),
    ActionItem("preview", "本地预览", "读取并总结 Capital 请求", "3"),
    ActionItem("plan", "生成计划", "根据目标、需求和可用性生成计划", "4"),
    ActionItem("health", "运行状态", "连接 workspace Capital 服务", "5"),
    ActionItem("current", "当前状态", "读取 Capital group 当前视图", "6"),
)

CAPITAL_KINDS = (
    SelectOption("funding-objective", "Funding objective"),
    SelectOption("capital-demand", "Capital demand"),
    SelectOption("availability", "Availability"),
)


class CapitalToolsScreen(Screen[None]):
    TITLE = "Kairos"
    SUB_TITLE = "首页 › 系统维护 › 业务工具 › Capital"
    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self) -> None:
        super().__init__()
        self._action = ""
        self._kind = ""
        self._objective = ""
        self._demand = ""

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label("Capital", id="page-title")
        yield ActionList(*CAPITAL_ACTIONS, id="capital-tool-actions")
        yield Label("选择 Capital 操作。", id="capital-tool-status")
        yield RichLog(id="capital-tool-result", wrap=True, highlight=False)
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        action = event.option.id
        if action is None:
            return
        self._action = action
        if action == "schema":
            self.app.push_screen(
                SelectDialog(
                    "Schema 类型",
                    (SelectOption("all", "全部"), *CAPITAL_KINDS),
                ),
                self._schema_kind,
            )
        elif action in {"doctor", "preview"}:
            self.app.push_screen(
                SelectDialog("请求类型", CAPITAL_KINDS), self._request_kind
            )
        elif action == "plan":
            self.app.push_screen(
                InputDialog("Funding objective 文件", value="funding-objective.json"),
                self._plan_objective,
            )
        elif action == "current":
            self.app.push_screen(InputDialog("Capital group ID"), self._group_selected)
        else:
            self._run(action)

    def _schema_kind(self, value: str | None) -> None:
        if value is not None:
            self._run("schema", [] if value == "all" else [value])

    def _request_kind(self, value: str | None) -> None:
        if value is None:
            return
        self._kind = value
        self.app.push_screen(InputDialog("Capital request 文件"), self._request_file)

    def _request_file(self, value: str | None) -> None:
        if value:
            self._run(self._action, ["--kind", self._kind, "--file", value])

    def _plan_objective(self, value: str | None) -> None:
        if not value:
            return
        self._objective = value
        self.app.push_screen(
            InputDialog("Capital demand 文件", value="capital-demand.json"),
            self._plan_demand,
        )

    def _plan_demand(self, value: str | None) -> None:
        if not value:
            return
        self._demand = value
        self.app.push_screen(
            InputDialog("Availability 文件", value="availability.json"),
            self._plan_availability,
        )

    def _plan_availability(self, value: str | None) -> None:
        if value:
            self._run(
                "plan",
                [
                    "--objective-file",
                    self._objective,
                    "--demand-file",
                    self._demand,
                    "--availability-file",
                    value,
                ],
            )

    def _group_selected(self, value: str | None) -> None:
        if value:
            self._run("current", [value])

    def _run(self, action: str, arguments: list[str] | None = None) -> None:
        self.query_one("#capital-tool-status", Label).update(
            f"正在执行：{action}；Ctrl+C 可取消等待…"
        )
        self.run_worker(
            lambda: self._execute(action, arguments or []),
            name=f"capital-tool-{action}",
            group="capital-tool",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _execute(self, action: str, arguments: list[str]) -> dict[str, Any]:
        owner = self.app.state.owner  # type: ignore[attr-defined]
        if owner is None:
            raise RuntimeError(self.app.state.load_error or "当前没有可用的 workspace")  # type: ignore[attr-defined]
        if action in {"schema", "doctor", "preview", "plan"}:
            return NativeCliApplication(owner).run(
                "capital", ["standalone", action, *arguments]
            )
        socket = owner.paths.process_socket("capital")
        if not socket.exists():
            raise RuntimeError("workspace Capital 服务尚未运行")
        client = CapitalSystemClient(
            socket, view_root=owner.paths.snapshots, timeout=30.0
        )
        if action == "health":
            return client.health()
        if action == "current":
            return client.current_metadata(arguments[0])
        raise RuntimeError(f"unknown Capital action: {action}")

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "capital-tool":
            return
        status = self.query_one("#capital-tool-status", Label)
        if event.state.name == "ERROR":
            status.update(f"操作失败：{event.worker.error}")
        elif event.state.name == "SUCCESS":
            status.update("操作完成")
            log = self.query_one("#capital-tool-result", RichLog)
            log.clear()
            log.write(Pretty(event.worker.result, expand_all=True))


INTEGRATION_CAPABILITIES = (
    ActionItem(
        "capabilities",
        "Provider 集成",
        "认证、Provider 连接与标准化外部事实由 Integration 所有",
        "1",
    ),
    ActionItem(
        "transfer",
        "Transfer",
        "查询并执行 Provider 支持的资产划转操作",
        "2",
    ),
    ActionItem(
        "earn",
        "Earn",
        "查询 Provider 支持的理财产品和申赎操作",
        "3",
    ),
)


class IntegrationCapabilitiesScreen(Screen[None]):
    TITLE = "Kairos"
    SUB_TITLE = "首页 › 系统维护 › 业务工具 › Provider 集成"
    BINDINGS = [Binding("escape", "back", "返回")]

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label("Provider 集成能力", id="page-title")
        yield ActionList(*INTEGRATION_CAPABILITIES, id="integration-actions")
        yield Label(
            "这里展示 Integration 的能力边界；具体操作要求显式参数，避免隐式交互。",
            id="integration-status",
        )
        yield RichLog(id="integration-result", wrap=True, highlight=False)
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        action = event.option.id
        details = {
            "capabilities": {
                "owner": "kairos-integration",
                "capabilities": ["provider authentication", "transfer", "earn"],
            },
            "transfer": {
                "capability": "transfer",
                "usage": "kairos integration transfer --help",
                "note": "划转参数和确认由 Provider Integration 合同定义。",
            },
            "earn": {
                "capability": "earn",
                "usage": "kairos integration earn --help",
                "note": "产品查询和申赎参数由 Provider Integration 合同定义。",
            },
        }
        if action not in details:
            return
        log = self.query_one("#integration-result", RichLog)
        log.clear()
        log.write(Pretty(details[action], expand_all=True))


__all__ = [
    "BusinessToolsScreen",
    "CapitalToolsScreen",
    "IntegrationCapabilitiesScreen",
    "RiskToolsScreen",
]
