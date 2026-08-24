"""Launch-instance Execution controls inside the shared Textual shell."""

from __future__ import annotations

from typing import Any

from rich.pretty import Pretty
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, Label, OptionList, RichLog, Select
from textual.worker import Worker

from kairospy.system.apps.components.application import NativeCliApplication

from ..dialogs import ConfirmDialog, InputDialog
from ..widgets import ActionItem, ActionList, WorkspaceHeader


EXECUTION_ACTIONS = (
    ActionItem("status", "服务状态", "读取 Execution 组件状态", "1"),
    ActionItem("snapshot", "运行时快照", "读取 Execution 当前状态", "2"),
    ActionItem("routes", "执行路由", "查看可用订单路由", "3"),
    ActionItem("orders", "全部订单", "查看当前视图中的订单", "4"),
    ActionItem("open-orders", "未完成订单", "查看仍在生命周期中的订单", "5"),
    ActionItem("history", "历史订单", "查看已终结订单", "6"),
    ActionItem("fills", "成交记录", "查看 Execution 成交事实", "7"),
    ActionItem("events", "生命周期事件", "查看 Execution 事件", "8"),
    ActionItem("audit", "审计记录", "查看 Execution 审计事实", "9"),
    ActionItem("inspect", "检查订单", "按 Order ID 查看当前订单", "i"),
    ActionItem("trace", "追踪订单", "按 Order ID 查看关联轨迹", "t"),
    ActionItem("journal", "订单 Journal", "按 Order ID 查看持久记录", "j"),
    ActionItem("reconcile", "请求对账", "让 Execution 与外部状态对账", "r"),
    ActionItem("submit", "提交订单", "通过当前 Launch Execution 提交", "s"),
    ActionItem("cancel", "撤销订单", "撤销当前 Launch 中的订单", "c"),
    ActionItem("replace", "修改订单", "替换订单数量或限价", "e"),
)


class ExecutionComponentScreen(Screen[None]):
    TITLE = "Kairos"
    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self, launch_id: str, instance_id: str, mode: str) -> None:
        super().__init__()
        self.launch_id = launch_id
        self.instance_id = instance_id
        self.mode = mode
        self._pending_action = ""
        self._pending_order_id = ""
        self._pending_quantity = ""
        self.sub_title = (
            f"首页 › 策略与运行 › {launch_id} › {instance_id} › Execution"
        )

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label("Execution", id="page-title")
        yield Label(
            f"{self.launch_id} / {self.instance_id} · {self.mode}",
            id="workspace-summary",
        )
        yield ActionList(*EXECUTION_ACTIONS, id="execution-actions")
        yield Label("选择 Execution 操作。", id="execution-status")
        yield RichLog(id="execution-result", wrap=True, highlight=False)
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        action = event.option.id
        if action is None:
            return
        if action in {"inspect", "trace", "journal", "cancel", "replace"}:
            self._pending_action = action
            self.app.push_screen(InputDialog("Order ID"), self._order_selected)
        elif action == "submit":
            self.app.push_screen(
                ExecutionSubmitScreen(self.launch_id, self.instance_id, self.mode),
                self._form_completed,
            )
        elif action == "reconcile":
            self._confirm(action, "请求 Execution 对账？")
        else:
            self._run(action)

    def _order_selected(self, value: str | None) -> None:
        if not value:
            return
        self._pending_order_id = value
        if self._pending_action == "cancel":
            self.app.push_screen(
                InputDialog("撤单原因", value="manual cancel"), self._cancel_reason
            )
        elif self._pending_action == "replace":
            self.app.push_screen(InputDialog("新数量"), self._replace_quantity)
        else:
            self._run(self._pending_action, ["--order-id", value])

    def _cancel_reason(self, value: str | None) -> None:
        if value is not None:
            self._confirm(
                "cancel",
                f"撤销订单 {self._pending_order_id}？",
                ["--order-id", self._pending_order_id, "--reason", value],
            )

    def _replace_quantity(self, value: str | None) -> None:
        if not value:
            return
        self._pending_quantity = value
        self.app.push_screen(
            InputDialog("新限价（可留空）"), self._replace_price
        )

    def _replace_price(self, value: str | None) -> None:
        arguments = [
            "--order-id",
            self._pending_order_id,
            "--quantity",
            self._pending_quantity,
        ]
        if value:
            arguments.extend(("--limit-price", value))
        self._confirm("replace", f"修改订单 {self._pending_order_id}？", arguments)

    def _confirm(
        self, action: str, message: str, arguments: list[str] | None = None
    ) -> None:
        if self.app.state.yes:  # type: ignore[attr-defined]
            self._run(action, arguments)
            return
        self.app.push_screen(
            ConfirmDialog("Execution 变更", message, confirm_label="继续"),
            lambda confirmed: self._run(action, arguments) if confirmed else None,
        )

    def _form_completed(self, result: dict[str, Any] | None) -> None:
        if result is not None:
            self._show(result)

    def _run(self, action: str, arguments: list[str] | None = None) -> None:
        state = self.app.state  # type: ignore[attr-defined]
        if action in {"reconcile", "cancel", "replace"} and (
            state.dry_run or state.no_exec
        ):
            self._show(
                {"status": "preview", "action": action, "arguments": arguments or []}
            )
            return
        self.query_one("#execution-status", Label).update(
            f"正在执行：{action}；Ctrl+C 可取消等待…"
        )
        self.run_worker(
            lambda: self._execute(action, arguments or []),
            name=f"execution-{action}",
            group="execution-action",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _execute(self, action: str, arguments: list[str]) -> dict[str, Any]:
        owner = self.app.state.owner  # type: ignore[attr-defined]
        if action == "status":
            instance = owner.instance(self.mode, self.launch_id, self.instance_id)
            from kairospy.system.apps.launch.application import LaunchRuntimeApplication

            return LaunchRuntimeApplication(owner).component_status(instance)[
                "execution"
            ]
        value = NativeCliApplication(owner).run(
            "execution",
            [
                "connected",
                "--mode",
                self.mode,
                "--launch-id",
                self.launch_id,
                "--instance-id",
                self.instance_id,
                action,
                *arguments,
            ],
        )
        return {
            **value,
            "launch_id": self.launch_id,
            "instance_id": self.instance_id,
            "mode": self.mode,
            "scope": "launch-instance",
        }

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "execution-action":
            return
        if event.state.name == "ERROR":
            self.query_one("#execution-status", Label).update(
                f"操作失败：{event.worker.error}"
            )
        elif event.state.name == "SUCCESS":
            self._show(event.worker.result)

    def _show(self, value: Any) -> None:
        self.query_one("#execution-status", Label).update("操作完成")
        log = self.query_one("#execution-result", RichLog)
        log.clear()
        log.write(Pretty(value, expand_all=True))


class ExecutionSubmitScreen(Screen[dict[str, Any] | None]):
    TITLE = "Kairos"
    BINDINGS = [Binding("escape", "cancel", "取消")]

    def __init__(self, launch_id: str, instance_id: str, mode: str) -> None:
        super().__init__()
        self.launch_id = launch_id
        self.instance_id = instance_id
        self.mode = mode
        self.sub_title = (
            f"首页 › 策略与运行 › {launch_id} › {instance_id} › 提交订单"
        )

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label("提交 Execution 订单", id="page-title")
        with VerticalScroll(id="execution-order-form"):
            for label, id_, placeholder in (
                ("Order ID", "order-id", "order-001"),
                ("Account ID", "order-account", "paper-main"),
                ("Segment key", "order-segment", "spot"),
                ("Instrument ID", "order-instrument", "instrument:..."),
                ("Quantity", "order-quantity", "1"),
                ("Execution route ID", "order-route", "paper-main-spot"),
            ):
                yield Label(label)
                yield Input(placeholder=placeholder, id=id_)
            yield Label("方向")
            yield Select(
                (("买入", "buy"), ("卖出", "sell")),
                value="buy",
                allow_blank=False,
                id="order-side",
            )
            yield Label("订单类型")
            yield Select(
                (("市价", "market"), ("限价", "limit")),
                value="market",
                allow_blank=False,
                id="order-type",
            )
            yield Label("限价（限价单必填）")
            yield Input(id="order-limit")
            yield Label("", id="execution-order-error")
            with Horizontal(classes="form-actions"):
                yield Button("取消", id="cancel")
                yield Button("提交订单", id="submit", variant="primary")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.action_cancel()
            return
        try:
            arguments = self._arguments()
        except ValueError as error:
            self.query_one("#execution-order-error", Label).update(str(error))
            return
        state = self.app.state  # type: ignore[attr-defined]
        if state.dry_run or state.no_exec:
            self.dismiss(
                {"status": "preview", "action": "submit", "arguments": arguments}
            )
            return
        if state.yes:
            self._submit(arguments)
            return
        self.app.push_screen(
            ConfirmDialog(
                "提交订单",
                f"账户：{self._input('order-account')}\n"
                f"标的：{self._input('order-instrument')}\n"
                f"数量：{self._input('order-quantity')}",
                confirm_label="提交",
            ),
            lambda confirmed: self._submit(arguments) if confirmed else None,
        )

    def _arguments(self) -> list[str]:
        values = {
            "--order-id": self._required("order-id"),
            "--account-id": self._required("order-account"),
            "--segment-key": self._required("order-segment"),
            "--instrument-id": self._required("order-instrument"),
            "--quantity": self._required("order-quantity"),
            "--execution-route-id": self._required("order-route"),
            "--side": self._select("order-side"),
            "--order-type": self._select("order-type"),
        }
        arguments = [item for pair in values.items() for item in pair]
        limit = self._input("order-limit")
        if values["--order-type"] == "limit":
            if not limit:
                raise ValueError("限价单必须填写限价")
            arguments.extend(("--limit-price", limit))
        return arguments

    def _submit(self, arguments: list[str]) -> None:
        self.query_one("#execution-order-error", Label).update("正在提交…")
        self.query_one("#submit", Button).disabled = True
        self.run_worker(
            lambda: NativeCliApplication(self.app.state.owner).run(  # type: ignore[attr-defined]
                "execution",
                [
                    "connected",
                    "--mode",
                    self.mode,
                    "--launch-id",
                    self.launch_id,
                    "--instance-id",
                    self.instance_id,
                    "submit",
                    *arguments,
                ],
            ),
            name="execution-submit",
            group="execution-submit",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "execution-submit":
            return
        self.query_one("#submit", Button).disabled = False
        if event.state.name == "ERROR":
            self.query_one("#execution-order-error", Label).update(
                f"提交失败：{event.worker.error}"
            )
        elif event.state.name == "SUCCESS":
            self.dismiss(event.worker.result or {})

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _input(self, id_: str) -> str:
        return self.query_one(f"#{id_}", Input).value.strip()

    def _required(self, id_: str) -> str:
        value = self._input(id_)
        if not value:
            raise ValueError("请填写所有必填项")
        return value

    def _select(self, id_: str) -> str:
        value = self.query_one(f"#{id_}", Select).value
        if value is Select.NULL:
            raise ValueError("请选择所有必填项")
        return str(value)
