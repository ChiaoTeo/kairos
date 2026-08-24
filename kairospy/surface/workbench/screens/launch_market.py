"""Launch-instance Market controls inside the unified workbench."""

from __future__ import annotations

from typing import Any

from rich.pretty import Pretty
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Label, OptionList, RichLog
from textual.worker import Worker

from kairospy.system.apps.launch.application import LaunchRuntimeApplication
from kairospy.system.apps.launch.application.connections import (
    resolve_instance_connections,
)
from kairospy.system.apps.components.application import NativeCliApplication

from ..dialogs import ConfirmDialog, InputDialog
from ..widgets import ActionItem, ActionList, WorkspaceHeader


MARKET_COMPONENT_ACTIONS = (
    ActionItem("status", "组件状态", "读取当前实例绑定的 Market 状态", "1"),
    ActionItem("routes", "数据路由", "查看当前实例可用 provider route", "2"),
    ActionItem("quote", "报价快照", "按 Market ID 读取当前报价", "3"),
    ActionItem("bar", "K 线快照", "按 Market ID 和周期读取 K 线", "4"),
    ActionItem("greeks", "Greeks 快照", "读取期权 Greeks", "5"),
    ActionItem("freshness", "行情新鲜度", "查看 observation 的更新时间", "6"),
    ActionItem("pause-replay", "暂停回放", "暂停该实例的 Market replay", "p"),
    ActionItem("resume-replay", "继续回放", "恢复该实例的 Market replay", "r"),
)


class LaunchMarketComponentScreen(Screen[None]):
    TITLE = "Kairos"
    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self, launch_id: str, instance_id: str, mode: str) -> None:
        super().__init__()
        self.launch_id = launch_id
        self.instance_id = instance_id
        self.mode = mode
        self._action = ""
        self._market_id = ""
        self._provider = ""
        self._timeframe = ""
        self.sub_title = (
            f"首页 › 策略与运行 › {launch_id} › {instance_id} › Market"
        )

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label("Market", id="page-title")
        yield Label(
            f"{self.launch_id} / {self.instance_id} · {self.mode}",
            id="workspace-summary",
        )
        yield ActionList(*MARKET_COMPONENT_ACTIONS, id="launch-market-actions")
        yield Label("选择 Market 操作。", id="launch-market-status")
        yield RichLog(id="launch-market-result", wrap=True, highlight=False)
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        action = event.option.id
        if action is None:
            return
        self._action = action
        if action in {"quote", "bar", "greeks", "freshness"}:
            selected = self.app.state.selected_market  # type: ignore[attr-defined]
            default = str(selected.id) if selected is not None else ""
            self.app.push_screen(
                InputDialog("Market ID", value=default, placeholder="market:..."),
                self._market_selected,
            )
        elif action in {"pause-replay", "resume-replay"}:
            self._confirm_replay(action)
        else:
            self._run(action)

    def _market_selected(self, value: str | None) -> None:
        if not value:
            return
        self._market_id = value
        self.app.push_screen(
            InputDialog("Provider（可留空使用当前 route）"),
            self._provider_selected,
        )

    def _provider_selected(self, value: str | None) -> None:
        if value is None:
            return
        self._provider = value
        if self._action == "bar":
            self.app.push_screen(
                InputDialog("K 线周期", value="1m"), self._timeframe_selected
            )
        elif self._action == "freshness":
            self.app.push_screen(
                InputDialog("Observation（可留空）", placeholder="quote / bar / greeks"),
                self._freshness_selected,
            )
        else:
            self._run(self._action)

    def _timeframe_selected(self, value: str | None) -> None:
        if not value:
            return
        self._timeframe = value
        self._run("bar")

    def _freshness_selected(self, value: str | None) -> None:
        if value is not None:
            self._timeframe = value
            self._run("freshness")

    def _confirm_replay(self, action: str) -> None:
        state = self.app.state  # type: ignore[attr-defined]
        if state.yes:
            self._run(action)
            return
        self.app.push_screen(
            ConfirmDialog(
                "Market replay",
                "该操作会改变当前 Launch Instance 的回放输入状态。",
                confirm_label="继续",
            ),
            lambda confirmed: self._run(action) if confirmed else None,
        )

    def _run(self, action: str) -> None:
        state = self.app.state  # type: ignore[attr-defined]
        if action in {"pause-replay", "resume-replay"} and (
            state.dry_run or state.no_exec
        ):
            self._show({"status": "preview", "action": action})
            return
        self.query_one("#launch-market-status", Label).update(
            f"正在执行：{action}；Ctrl+C 可取消等待…"
        )
        self.run_worker(
            lambda: self._execute(action),
            name=f"launch-market-{action}",
            group="launch-market",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _execute(self, action: str) -> dict[str, Any]:
        owner = self.app.state.owner  # type: ignore[attr-defined]
        instance = owner.instance(self.mode, self.launch_id, self.instance_id)
        if action == "status":
            value = LaunchRuntimeApplication(owner).component_status(instance)["market"]
        else:
            connection = resolve_instance_connections(instance).market
            if connection is None:
                raise RuntimeError("当前实例没有绑定 Market 组件")
            if not connection.socket.exists():
                raise RuntimeError("当前实例的 Market socket 不可用")
            target = ["--socket", str(connection.socket)]
            if connection.view_root is not None:
                target.extend(("--view-root", str(connection.view_root)))
            arguments: list[str] = []
            command = action
            if action in {"quote", "bar", "greeks"}:
                command = "snapshot"
                arguments.extend((action, "--market-id", self._market_id))
                if self._provider:
                    arguments.extend(("--provider", self._provider))
                if action == "bar":
                    arguments.extend(("--timeframe", self._timeframe))
            elif action == "freshness":
                arguments.extend(("--market-id", self._market_id))
                if self._provider:
                    arguments.extend(("--provider", self._provider))
                if self._timeframe:
                    arguments.extend(("--observation", self._timeframe))
            value = NativeCliApplication(owner).run(
                "market", ["connected", command, *target, *arguments]
            )
        return {
            **value,
            "launch_id": self.launch_id,
            "instance_id": self.instance_id,
            "mode": self.mode,
            "scope": "launch-instance",
        }

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "launch-market":
            return
        if event.state.name == "ERROR":
            self.query_one("#launch-market-status", Label).update(
                f"操作失败：{event.worker.error}"
            )
        elif event.state.name == "SUCCESS":
            self._show(event.worker.result)

    def _show(self, value: Any) -> None:
        self.query_one("#launch-market-status", Label).update("操作完成")
        log = self.query_one("#launch-market-result", RichLog)
        log.clear()
        log.write(Pretty(value, expand_all=True))
