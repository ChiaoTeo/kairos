"""Account facts and standalone order workflows in the unified workbench."""

from __future__ import annotations

import json
from typing import Any

from rich.pretty import Pretty
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, Label, OptionList, RichLog, Select
from textual.worker import Worker

from kairospy.investment.apps.account.application import AccountConfigurationApplication
from kairospy.investment.apps.account.application.cli import AccountCliApplication
from kairospy.system.apps.components.application import NativeCliApplication

from ..dialogs import ConfirmDialog, InputDialog
from ..widgets import ActionItem, ActionList, WorkspaceHeader


ACCOUNT_ACTIONS = (
    ActionItem("overview", "账户概览", "读取账户汇总事实", "1"),
    ActionItem("assets", "资产与余额", "读取当前余额", "2"),
    ActionItem("positions", "交易仓位", "读取当前持仓", "3"),
    ActionItem("orders", "订单管理", "查询或操作直接 provider 订单", "4"),
    ActionItem("earn-holdings", "理财与质押", "读取 Earn holdings", "5"),
    ActionItem("fees", "费率与账户等级", "按产品与交易对查询费率", "6"),
    ActionItem("transfer", "资金划转", "查看当前能力与安全限制", "7"),
    ActionItem("doctor", "账户诊断", "检查配置、凭据与连接", "8"),
)


class AccountOperationsScreen(Screen[None]):
    TITLE = "Kairos"
    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self, record: dict[str, Any]) -> None:
        super().__init__()
        self.record = record
        self.account_id = str(record["account_id"])
        self._fee_product = "spot"
        self.sub_title = f"首页 › 运行资源 › 交易账户 › {self.account_id} › 运行查询"

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label(f"账户运行查询 · {self.account_id}", id="page-title")
        yield ActionList(*ACCOUNT_ACTIONS, id="account-operation-actions")
        yield Label("选择账户查询或订单操作。", id="account-operation-status")
        yield RichLog(id="account-operation-result", wrap=True, highlight=False)
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        action = event.option.id
        if action == "orders":
            self.app.push_screen(AccountOrdersScreen(self.record))
        elif action == "fees":
            self.app.push_screen(
                InputDialog("产品", value="spot"), self._fee_product_selected
            )
        elif action == "transfer":
            capabilities = set(map(str, self.record.get("capabilities") or ()))
            self._show(
                {
                    "account_id": self.account_id,
                    "transfer_available": "transfer" in capabilities,
                    "status": "preview_required",
                    "message": "资金划转尚未开放执行；必须先 preview 再确认。",
                }
            )
        elif action is not None:
            self._run(action)

    def _fee_product_selected(self, value: str | None) -> None:
        if not value:
            return
        self._fee_product = value
        self.app.push_screen(
            InputDialog("Provider symbol", value="BTCUSDT"), self._fee_symbol_selected
        )

    def _fee_symbol_selected(self, value: str | None) -> None:
        if value:
            self._run("fees", ["--product", self._fee_product, "--symbol", value])

    def _run(self, action: str, arguments: list[str] | None = None) -> None:
        self.query_one("#account-operation-status", Label).update(
            f"正在执行：{action}；Ctrl+C 可取消等待…"
        )
        self.run_worker(
            lambda: self._execute(action, arguments or []),
            name=f"account-{action}",
            group="account-operation",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _execute(self, action: str, arguments: list[str]) -> Any:
        owner = self.app.state.owner  # type: ignore[attr-defined]
        if action == "doctor":
            return AccountConfigurationApplication(owner).doctor(self.account_id)
        return AccountCliApplication(owner).run(
            [
                "--account-id",
                self.account_id,
                "standalone",
                action,
                *arguments,
            ]
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "account-operation":
            return
        if event.state.name == "ERROR":
            self.query_one("#account-operation-status", Label).update(
                f"操作失败：{event.worker.error}"
            )
        elif event.state.name == "SUCCESS":
            self.query_one("#account-operation-status", Label).update("操作完成")
            self._show(event.worker.result)

    def _show(self, value: Any) -> None:
        log = self.query_one("#account-operation-result", RichLog)
        log.clear()
        log.write(Pretty(value, expand_all=True))


ORDER_ACTIONS = (
    ActionItem("open-orders", "未完成订单", "读取当前 open orders", "1"),
    ActionItem("history", "历史订单", "按 provider symbol 查询历史", "2"),
    ActionItem("fills", "成交记录", "按 provider symbol 查询成交", "3"),
    ActionItem("order", "查询订单", "按 Order ID 查询详情", "4"),
    ActionItem("submit", "下单", "直接向当前账户 provider 提交", "5"),
    ActionItem("cancel", "撤单", "按 Order ID 撤销订单", "6"),
    ActionItem("replace", "修改订单", "撤换为新订单参数", "7"),
)


class AccountOrdersScreen(Screen[None]):
    TITLE = "Kairos"
    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self, record: dict[str, Any]) -> None:
        super().__init__()
        self.record = record
        self.account_id = str(record["account_id"])
        self.segment = str(next(iter(record.get("segments") or ("spot",)), "spot"))
        self._action = ""
        self._order_id = ""
        self.sub_title = f"首页 › 运行资源 › 交易账户 › {self.account_id} › 订单管理"

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label(f"订单管理 · {self.account_id}", id="page-title")
        yield Label(
            f"scope=direct-provider · segment={self.segment}",
            id="workspace-summary",
        )
        yield ActionList(*ORDER_ACTIONS, id="account-order-actions")
        yield Label("选择订单操作。", id="account-order-status")
        yield RichLog(id="account-order-result", wrap=True, highlight=False)
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        action = event.option.id
        if action is None:
            return
        self._action = action
        if action == "open-orders":
            self._run(action)
        elif action in {"history", "fills"}:
            self.app.push_screen(
                InputDialog("Provider symbol（可留空）", value="BTCUSDT"),
                self._symbol_selected,
            )
        elif action in {"order", "cancel", "replace"}:
            self.app.push_screen(InputDialog("Order ID"), self._order_selected)
        elif action == "submit":
            self.app.push_screen(
                StandaloneOrderForm(self.record, self.segment), self._form_completed
            )

    def _symbol_selected(self, value: str | None) -> None:
        if value is not None:
            self._run(self._action, ["--symbol", value] if value else [])

    def _order_selected(self, value: str | None) -> None:
        if not value:
            return
        self._order_id = value
        if self._action == "replace":
            self.app.push_screen(
                StandaloneOrderForm(
                    self.record, self.segment, target_order_id=value
                ),
                self._form_completed,
            )
        elif self._action == "cancel":
            self._confirm_write("cancel", ["--order-id", value])
        else:
            self.app.push_screen(
                InputDialog("Provider symbol（可留空）"),
                lambda symbol: self._run(
                    "order",
                    ["--order-id", value]
                    + (["--symbol", symbol] if symbol else []),
                )
                if symbol is not None
                else None,
            )

    def _confirm_write(self, action: str, arguments: list[str]) -> None:
        state = self.app.state  # type: ignore[attr-defined]
        if state.yes:
            self._run(action, arguments, write=True)
            return
        self.app.push_screen(
            ConfirmDialog(
                "直接 Provider 订单操作",
                f"账户：{self.account_id}\n环境：{self.record.get('environment')}\n"
                f"操作：{action}",
                confirm_label="继续",
            ),
            lambda confirmed: self._run(action, arguments, write=True)
            if confirmed
            else None,
        )

    def _form_completed(self, value: dict[str, Any] | None) -> None:
        if value is not None:
            self._show(value)

    def _run(
        self, action: str, arguments: list[str] | None = None, *, write: bool = False
    ) -> None:
        state = self.app.state  # type: ignore[attr-defined]
        if write and (state.dry_run or state.no_exec):
            self._show({"status": "preview", "action": action, "arguments": arguments})
            return
        self.query_one("#account-order-status", Label).update(
            f"正在执行：{action}；Ctrl+C 可取消等待…"
        )
        self.run_worker(
            lambda: self._execute(action, arguments or [], write=write),
            name=f"account-order-{action}",
            group="account-order",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _execute(self, action: str, arguments: list[str], *, write: bool) -> Any:
        owner = self.app.state.owner  # type: ignore[attr-defined]
        binding = AccountCliApplication(owner).run(
            [
                "standalone",
                "trading-binding",
                "--account-id",
                self.account_id,
                "--access",
                "trade" if write else "read",
                "--segment",
                self.segment,
            ]
        )
        return NativeCliApplication(owner).run(
            "execution",
            [
                "standalone",
                "--binding-json",
                json.dumps(binding),
                *(["--confirm-live"] if write else []),
                action,
                *arguments,
            ],
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "account-order":
            return
        if event.state.name == "ERROR":
            self.query_one("#account-order-status", Label).update(
                f"操作失败：{event.worker.error}"
            )
        elif event.state.name == "SUCCESS":
            self.query_one("#account-order-status", Label).update("操作完成")
            self._show(event.worker.result)

    def _show(self, value: Any) -> None:
        log = self.query_one("#account-order-result", RichLog)
        log.clear()
        log.write(Pretty(value, expand_all=True))


class StandaloneOrderForm(Screen[dict[str, Any] | None]):
    TITLE = "Kairos"
    BINDINGS = [Binding("escape", "cancel", "取消")]

    def __init__(
        self,
        record: dict[str, Any],
        segment: str,
        *,
        target_order_id: str | None = None,
    ) -> None:
        super().__init__()
        self.record = record
        self.account_id = str(record["account_id"])
        self.segment = segment
        self.target_order_id = target_order_id
        self.sub_title = f"首页 › 运行资源 › 交易账户 › {self.account_id} › 订单表单"

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label("修改订单" if self.target_order_id else "提交订单", id="page-title")
        with VerticalScroll(id="standalone-order-form"):
            for label, id_, placeholder in (
                ("新 Order ID", "standalone-order-id", "order-001"),
                ("Instrument ID", "standalone-instrument", "instrument:..."),
                ("Provider symbol", "standalone-symbol", "BTCUSDT"),
                ("Quantity", "standalone-quantity", "1"),
            ):
                yield Label(label)
                yield Input(placeholder=placeholder, id=id_)
            yield Label("方向")
            yield Select(
                (("买入", "buy"), ("卖出", "sell")),
                value="buy",
                allow_blank=False,
                id="standalone-side",
            )
            yield Label("订单类型")
            yield Select(
                (("市价", "market"), ("限价", "limit")),
                value="limit" if self.target_order_id else "market",
                allow_blank=False,
                id="standalone-type",
            )
            yield Label("限价（可留空）")
            yield Input(id="standalone-limit")
            yield Label("", id="standalone-order-error")
            with Horizontal(classes="form-actions"):
                yield Button("取消", id="standalone-cancel")
                yield Button("确认", id="standalone-submit", variant="primary")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "standalone-cancel":
            self.action_cancel()
            return
        try:
            arguments = self._arguments()
        except ValueError as error:
            self.query_one("#standalone-order-error", Label).update(str(error))
            return
        state = self.app.state  # type: ignore[attr-defined]
        action = "replace" if self.target_order_id else "submit"
        if state.dry_run or state.no_exec:
            self.dismiss({"status": "preview", "action": action, "arguments": arguments})
            return
        if state.yes:
            self._submit(action, arguments)
            return
        self.app.push_screen(
            ConfirmDialog(
                "直接 Provider 订单操作",
                f"账户：{self.account_id}\n环境：{self.record.get('environment')}",
                confirm_label="继续",
            ),
            lambda confirmed: self._submit(action, arguments) if confirmed else None,
        )

    def _arguments(self) -> list[str]:
        arguments = [
            "--order-id",
            self._required("standalone-order-id"),
            "--instrument-id",
            self._required("standalone-instrument"),
            "--symbol",
            self._required("standalone-symbol"),
            "--quantity",
            self._required("standalone-quantity"),
            "--side",
            self._select("standalone-side"),
            "--order-type",
            self._select("standalone-type"),
        ]
        if self.target_order_id:
            arguments[0:0] = ["--target-order-id", self.target_order_id]
        limit = self._input("standalone-limit")
        if self._select("standalone-type") == "limit" and not limit:
            raise ValueError("限价单必须填写限价")
        if limit:
            arguments.extend(("--limit-price", limit))
        return arguments

    def _submit(self, action: str, arguments: list[str]) -> None:
        self.query_one("#standalone-submit", Button).disabled = True
        self.run_worker(
            lambda: self._execute(action, arguments),
            name="standalone-order-submit",
            group="standalone-order-submit",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _execute(self, action: str, arguments: list[str]) -> dict[str, Any]:
        owner = self.app.state.owner  # type: ignore[attr-defined]
        binding = AccountCliApplication(owner).run(
            [
                "standalone",
                "trading-binding",
                "--account-id",
                self.account_id,
                "--access",
                "trade",
                "--segment",
                self.segment,
            ]
        )
        return NativeCliApplication(owner).run(
            "execution",
            [
                "standalone",
                "--binding-json",
                json.dumps(binding),
                "--confirm-live",
                action,
                *arguments,
            ],
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "standalone-order-submit":
            return
        self.query_one("#standalone-submit", Button).disabled = False
        if event.state.name == "ERROR":
            self.query_one("#standalone-order-error", Label).update(
                f"操作失败：{event.worker.error}"
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
