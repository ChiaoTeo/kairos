"""Launch configuration and runtime screens."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from rich.pretty import Pretty
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Label, OptionList, RichLog, TextArea
from textual.worker import Worker

from kairospy.system.apps.launch.application import (
    LaunchConfigurationApplication,
    LaunchInstanceTimelineApplication,
    LaunchRegistryApplication,
    LaunchRuntimeApplication,
)
from kairospy.system.apps.launch.application.wizard import load_values
from kairospy.system.apps.components.application.supervisor import UnixRestClient

from ..dialogs import ConfirmDialog, InputDialog
from ..widgets import ActionItem, ActionList, WorkspaceHeader
from .execution import ExecutionComponentScreen
from .launch_market import LaunchMarketComponentScreen
from .launch_setup import LaunchSetupScreen


class StrategyScreen(Screen[None]):
    """List configured and previously run Launch definitions."""

    TITLE = "Kairos"
    SUB_TITLE = "首页 › 策略与运行"
    BINDINGS = [
        Binding("escape", "back", "返回"),
        Binding("r", "refresh", "刷新"),
        Binding("n", "new", "新建"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._launches: dict[str, dict[str, Any]] = {}

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label("配置并运行策略", id="page-title")
        yield Label("正在读取 Launch…", id="strategy-status")
        yield DataTable(id="launch-table", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#launch-table", DataTable).add_columns(
            "Launch", "模式", "状态", "实例", "配置"
        )
        self.action_refresh()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self.query_one("#strategy-status", Label).update("正在读取 Launch…")
        self.run_worker(
            self._load_launches,
            name="launch-list",
            group="launch-list",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _load_launches(self) -> tuple[dict[str, Any], ...]:
        state = self.app.state  # type: ignore[attr-defined]
        if state.owner is None:
            raise RuntimeError(state.load_error or "当前没有可用的 workspace")
        by_id: dict[str, dict[str, Any]] = {}
        for entry in LaunchRegistryApplication(state.owner).list():
            launch_id = str(entry.get("launch_id") or "")
            if launch_id:
                by_id[launch_id] = dict(entry)
        config_root = state.owner.paths.launch_config("_").parent
        for path in sorted(config_root.glob("*.toml")):
            launch_id = path.stem
            entry = by_id.setdefault(launch_id, {"launch_id": launch_id})
            entry.setdefault("config", str(path))
            try:
                config = LaunchConfigurationApplication().load(
                    path, workspace_root=state.owner.paths.root
                )
                entry.setdefault("mode", config.mode)
            except (OSError, ValueError):
                entry.setdefault("mode", "—")
        for draft in LaunchConfigurationApplication().list_drafts(
            state.owner.paths.root
        ):
            launch_id = str(draft["launch_id"])
            entry = by_id.setdefault(launch_id, {"launch_id": launch_id})
            entry.update(
                {
                    "state": str(draft["status"]),
                    "config": str(draft["path"]),
                    "draft": True,
                }
            )
        return tuple(by_id[key] for key in sorted(by_id))

    def action_new(self) -> None:
        self.app.push_screen(
            InputDialog("Launch id", placeholder="new-launch"),
            self._open_new_launch,
        )

    def _open_new_launch(self, value: str | None) -> None:
        if not value:
            return
        state = self.app.state  # type: ignore[attr-defined]
        if state.owner.paths.launch_config(value).exists():
            self.notify("同名 Launch 配置已经存在。", severity="error")
            return
        draft = LaunchConfigurationApplication().draft_path(state.owner.paths.root, value)
        self.app.push_screen(
            LaunchSetupScreen(value, draft if draft.is_file() else None),
            lambda result: self.action_refresh() if result is not None else None,
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "launch-list":
            return
        status = self.query_one("#strategy-status", Label)
        if event.state.name == "ERROR":
            status.update(f"读取失败：{event.worker.error}")
            return
        if event.state.name != "SUCCESS":
            return
        records = tuple(event.worker.result or ())
        table = self.query_one("#launch-table", DataTable)
        table.clear()
        self._launches = {str(item["launch_id"]): item for item in records}
        for item in records:
            launch_id = str(item["launch_id"])
            table.add_row(
                launch_id,
                str(item.get("mode") or "—"),
                str(item.get("state") or "未运行"),
                str(item.get("instance_id") or "—"),
                "已配置" if item.get("config") else "未找到",
                key=launch_id,
            )
        status.update(
            "没有 Launch 配置。" if not records else f"共 {len(records)} 个 Launch"
        )
        if records:
            table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        record = self._launches.get(str(event.row_key.value))
        if record is not None:
            self.app.state.selected_launch = str(record["launch_id"])  # type: ignore[attr-defined]
            self.app.push_screen(LaunchDetailScreen(record))


LAUNCH_ACTIONS = (
    ActionItem("validate", "校验配置", "检查配置结构和所需资源", "1"),
    ActionItem("status", "查看运行状态", "读取策略与依赖组件状态", "2"),
    ActionItem("start", "启动", "按当前配置启动新的运行实例", "3"),
    ActionItem("stop", "停止", "停止策略并释放运行资源", "4"),
    ActionItem("report", "查看回测报告", "读取最近完成的回测结果", "5"),
    ActionItem("instances", "查看运行实例", "列出当前及历史实例", "6"),
    ActionItem("logs", "查看日志", "读取最近的策略进程日志", "7"),
    ActionItem("wait", "等待回测完成", "等待并读取回测报告", "8"),
    ActionItem("restart", "重启", "停止当前实例并从当前配置启动新实例", "9"),
    ActionItem("edit", "编辑配置", "修改常用字段并保留高级配置", "e"),
    ActionItem("config", "查看配置", "解释规范化配置和运行计划", "0"),
    ActionItem("attach", "跟随运行输出", "持续刷新状态和策略日志", "a"),
    ActionItem("timeline", "查看实例时间线", "读取生命周期审计记录", "t"),
)


class LaunchDetailScreen(Screen[None]):
    """Operate one Launch through Launch-owned Application APIs."""

    TITLE = "Kairos"
    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self, record: dict[str, Any]) -> None:
        super().__init__()
        self.record = record
        self.launch_id = str(record["launch_id"])
        self.sub_title = f"首页 › 策略与运行 › {self.launch_id}"

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label(self.launch_id, id="page-title")
        yield Label(
            f"{self.record.get('mode') or '—'}  ·  {self.record.get('state') or '未运行'}",
            id="workspace-summary",
        )
        yield ActionList(*LAUNCH_ACTIONS, id="launch-actions")
        yield Label("选择操作。", id="launch-status")
        yield RichLog(id="launch-result", wrap=True, highlight=False)
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        action = event.option.id
        if action is None:
            return
        if action == "instances":
            self.app.push_screen(LaunchInstancesScreen(self.launch_id))
            return
        if action == "attach":
            self.app.push_screen(LaunchAttachScreen(self.launch_id))
            return
        if action == "config" and self.record.get("draft"):
            self._open_editor()
            return
        if action == "edit":
            self._open_editor()
            return
        if action in {"start", "stop", "restart"} and not self.app.state.yes:  # type: ignore[attr-defined]
            verb = {"start": "启动", "stop": "停止", "restart": "重启"}[action]
            self.app.push_screen(
                ConfirmDialog(
                    f"{verb} {self.launch_id}",
                    f"确认{verb}这个 Launch？",
                    confirm_label=verb,
                ),
                lambda confirmed: self._run_action(action) if confirmed else None,
            )
            return
        self._run_action(action)

    def _open_editor(self) -> None:
        self.app.push_screen(
            LaunchSetupScreen(self.launch_id, self._config_path()),
            self._configuration_saved,
        )

    def _configuration_saved(self, result: dict[str, Any] | None) -> None:
        if result is None:
            return
        self.record["config"] = str(result.get("path") or self._config_path())
        self.record["draft"] = result.get("status") != "published"
        self._show_result(result)

    def _run_action(self, action: str) -> None:
        state = self.app.state  # type: ignore[attr-defined]
        if action in {"start", "stop", "restart"} and (state.dry_run or state.no_exec):
            self._show_result(
                {"status": "preview", "action": action, "launch_id": self.launch_id}
            )
            return
        self.query_one("#launch-status", Label).update(f"正在执行：{action}…")
        self.run_worker(
            lambda: self._execute(action),
            name=f"launch-{action}",
            group="launch-action",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _config_path(self) -> Path:
        state = self.app.state  # type: ignore[attr-defined]
        configured = self.record.get("config")
        return (
            Path(configured)
            if configured
            else state.owner.paths.launch_config(self.launch_id)
        )

    def _execute(self, action: str) -> Any:
        state = self.app.state  # type: ignore[attr-defined]
        owner = state.owner
        if owner is None:
            raise RuntimeError(state.load_error or "当前没有可用的 workspace")
        runtime = LaunchRuntimeApplication(owner)
        instance = self.record.get("instance_id")
        if action == "validate":
            return LaunchConfigurationApplication().validate(
                self._config_path(), workspace_root=owner.paths.root
            )
        if action == "start":
            config = LaunchConfigurationApplication().load(
                self._config_path(), workspace_root=owner.paths.root
            )
            return runtime.start(config)
        if action == "status":
            return runtime.status(
                self.launch_id, instance=str(instance) if instance else None
            )
        if action == "stop":
            return runtime.stop(
                self.launch_id,
                instance=str(instance) if instance else None,
                mode=str(self.record.get("mode")) if self.record.get("mode") else None,
            )
        if action == "report":
            return runtime.report(
                self.launch_id, instance=str(instance) if instance else None
            )
        if action == "instances":
            return LaunchRegistryApplication(owner).instances(self.launch_id)
        if action == "logs":
            return runtime.logs(
                self.launch_id, instance=str(instance) if instance else None
            )
        if action == "wait":
            return runtime.wait(
                self.launch_id, instance=str(instance) if instance else None
            )
        if action == "restart":
            return runtime.restart(
                self.launch_id,
                instance=str(instance) if instance else None,
                config_path=self._config_path(),
            )
        if action == "config":
            path = self._config_path()
            if self.record.get("draft"):
                return {
                    "draft": load_values(path),
                    "readiness": LaunchConfigurationApplication().validate(
                        path, workspace_root=owner.paths.root
                    ),
                }
            return LaunchConfigurationApplication().explain(
                path, workspace_root=owner.paths.root
            )
        if action == "timeline":
            if not instance:
                raise ValueError("请先选择一个运行实例")
            workspace = owner.instance(
                str(self.record.get("mode")), self.launch_id, str(instance)
            )
            return LaunchInstanceTimelineApplication(workspace).list(limit=200)
        raise RuntimeError(f"unknown launch action: {action}")

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "launch-action":
            return
        if event.state.name == "ERROR":
            self.query_one("#launch-status", Label).update(
                f"执行失败：{event.worker.error}"
            )
        elif event.state.name == "SUCCESS":
            self._show_result(event.worker.result)

    def _show_result(self, value: Any) -> None:
        self.query_one("#launch-status", Label).update("操作完成")
        log = self.query_one("#launch-result", RichLog)
        log.clear()
        log.write(Pretty(value, expand_all=True))


class LaunchAttachScreen(Screen[None]):
    """Follow one active Launch without opening a second terminal UI."""

    TITLE = "Kairos"
    BINDINGS = [
        Binding("escape", "back", "返回"),
        Binding("space", "toggle_pause", "暂停/继续"),
        Binding("r", "refresh", "刷新"),
    ]

    def __init__(self, launch_id: str) -> None:
        super().__init__()
        self.launch_id = launch_id
        self.sub_title = f"首页 › 策略与运行 › {launch_id} › 跟随输出"
        self._paused = False
        self._seen: tuple[str, ...] = ()

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label(f"跟随运行输出 · {self.launch_id}", id="page-title")
        yield Label("正在连接…", id="attach-status")
        yield RichLog(id="attach-result", wrap=True, highlight=False, markup=True)
        yield Label("Strategy Python（可选）", classes="panel-title")
        yield TextArea(id="attach-python", language="python", show_line_numbers=True)
        yield Button("发送到当前 Strategy", id="attach-python-run", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(1.0, self.action_refresh)
        self.action_refresh()

    def action_back(self) -> None:
        self.workers.cancel_all()
        self.app.pop_screen()

    def action_toggle_pause(self) -> None:
        self._paused = not self._paused
        self.query_one("#attach-status", Label).update(
            "已暂停；Space 继续" if self._paused else "已继续"
        )

    def action_refresh(self) -> None:
        if self._paused:
            return
        self.run_worker(
            self._load,
            name="launch-attach-refresh",
            group="launch-attach",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "attach-python-run":
            return
        source = self.query_one("#attach-python", TextArea).text.strip()
        if not source:
            self.query_one("#attach-status", Label).update("请输入 Python 代码")
            return
        self.query_one("#attach-python-run", Button).disabled = True
        self.run_worker(
            lambda: asyncio.run(self._send_python(source)),
            name="launch-attach-python",
            group="launch-python",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    async def _send_python(self, source: str) -> dict[str, Any]:
        owner = self.app.state.owner  # type: ignore[attr-defined]
        active = LaunchRuntimeApplication(owner).running_instance(self.launch_id)
        if active is None:
            raise RuntimeError(f"Launch 未在运行：{self.launch_id}")
        instance_id = str(active["instance_id"])
        mode = str(active.get("mode") or "paper")
        socket_path = owner.instance(mode, self.launch_id, instance_id).socket("strategy")
        return await UnixRestClient(socket_path).request(
            "POST",
            "/v1/command",
            json.dumps(
                {
                    "request_id": f"workbench:{instance_id}",
                    "kind": "interactive.python",
                    "source": source,
                },
                separators=(",", ":"),
            ).encode("utf-8"),
        )

    def _load(self) -> dict[str, Any]:
        application = LaunchRuntimeApplication(self.app.state.owner)  # type: ignore[attr-defined]
        active = application.running_instance(self.launch_id)
        if active is None:
            raise RuntimeError(f"Launch 未在运行：{self.launch_id}")
        instance = str(active["instance_id"])
        return {
            "status": application.status(self.launch_id, instance=instance),
            "logs": application.logs(self.launch_id, instance=instance, lines=300),
        }

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group == "launch-python":
            self.query_one("#attach-python-run", Button).disabled = False
            if event.state.name == "ERROR":
                self.query_one("#attach-status", Label).update(
                    f"Python 执行失败：{event.worker.error}"
                )
            elif event.state.name == "SUCCESS":
                result = event.worker.result or {}
                log = self.query_one("#attach-result", RichLog)
                log.write(Pretty(result, expand_all=True))
                self.query_one("#attach-status", Label).update("Python 执行完成")
            return
        if event.worker.group != "launch-attach":
            return
        status = self.query_one("#attach-status", Label)
        if event.state.name == "ERROR":
            status.update(f"连接失败：{event.worker.error}")
            return
        if event.state.name != "SUCCESS":
            return
        value = event.worker.result or {}
        runtime = value.get("status") or {}
        status.update(
            f"{runtime.get('status') or runtime.get('state') or 'unknown'}"
            f" · {runtime.get('instance_id') or '—'} · Space 暂停"
        )
        lines = tuple(str(line) for line in (value.get("logs") or {}).get("lines", ()))
        if not lines:
            return
        common = 0
        maximum = min(len(self._seen), len(lines))
        for overlap in range(maximum, 0, -1):
            if self._seen[-overlap:] == lines[:overlap]:
                common = overlap
                break
        log = self.query_one("#attach-result", RichLog)
        for line in lines[common:]:
            log.write(line)
        self._seen = lines


class LaunchInstancesScreen(Screen[None]):
    """Select an explicit Launch instance before opening scoped controls."""

    TITLE = "Kairos"
    BINDINGS = [Binding("escape", "back", "返回"), Binding("r", "refresh", "刷新")]

    def __init__(self, launch_id: str) -> None:
        super().__init__()
        self.launch_id = launch_id
        self.sub_title = f"首页 › 策略与运行 › {launch_id} › 运行实例"
        self._instances: dict[str, dict[str, Any]] = {}

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label(f"{self.launch_id} · 运行实例", id="page-title")
        yield Label("正在读取实例…", id="instances-status")
        yield DataTable(id="instances-table", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#instances-table", DataTable).add_columns(
            "Instance", "模式", "状态", "创建时间", "更新时间"
        )
        self.action_refresh()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self.run_worker(
            lambda: LaunchRegistryApplication(self.app.state.owner).instances(  # type: ignore[attr-defined]
                self.launch_id
            ),
            name="launch-instances",
            group="launch-instances",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "launch-instances":
            return
        status = self.query_one("#instances-status", Label)
        if event.state.name == "ERROR":
            status.update(f"读取失败：{event.worker.error}")
            return
        if event.state.name != "SUCCESS":
            return
        records = list(event.worker.result or ())
        table = self.query_one("#instances-table", DataTable)
        table.clear()
        self._instances.clear()
        for index, record in enumerate(records):
            instance_id = str(record.get("instance_id") or "")
            mode = str(record.get("mode") or "")
            if not instance_id or not mode:
                continue
            key = f"{mode}:{instance_id}:{index}"
            self._instances[key] = dict(record)
            table.add_row(
                instance_id,
                mode,
                str(record.get("state") or "unknown"),
                str(record.get("created_at") or "—"),
                str(record.get("updated_at") or "—"),
                key=key,
            )
        status.update(
            "没有已注册实例。"
            if not self._instances
            else f"共 {len(self._instances)} 个实例"
        )
        if self._instances:
            table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        record = self._instances.get(str(event.row_key.value))
        if record is None:
            return
        state = self.app.state  # type: ignore[attr-defined]
        state.selected_launch = self.launch_id
        state.selected_launch_instance = str(record["instance_id"])
        state.selected_launch_mode = str(record["mode"])
        self.app.push_screen(LaunchInstanceScreen(self.launch_id, record))


INSTANCE_ACTIONS = (
    ActionItem("overview", "实例概览", "读取运行状态和注册信息", "1"),
    ActionItem("components", "实例组件", "查看 Market、Execution、Risk 等组件", "2"),
    ActionItem("timeline", "实例时间线", "查看并导出生命周期审计记录", "3"),
)


class LaunchInstanceScreen(Screen[None]):
    TITLE = "Kairos"
    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self, launch_id: str, record: dict[str, Any]) -> None:
        super().__init__()
        self.launch_id = launch_id
        self.record = record
        self.instance_id = str(record["instance_id"])
        self.mode = str(record["mode"])
        self.sub_title = (
            f"首页 › 策略与运行 › {launch_id} › 运行实例 › {self.instance_id}"
        )

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label(f"{self.launch_id} / {self.instance_id}", id="page-title")
        yield Label(
            f"{self.mode} · {self.record.get('state') or 'unknown'}",
            id="workspace-summary",
        )
        yield ActionList(*INSTANCE_ACTIONS, id="instance-actions")
        yield Label("选择实例操作。", id="instance-status")
        yield RichLog(id="instance-result", wrap=True, highlight=False)
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        action = event.option.id
        if action == "components":
            self.app.push_screen(
                LaunchComponentsScreen(self.launch_id, self.instance_id, self.mode)
            )
        elif action == "timeline":
            self.app.push_screen(
                LaunchTimelineScreen(self.launch_id, self.instance_id, self.mode)
            )
        elif action == "overview":
            self.run_worker(
                lambda: LaunchRuntimeApplication(self.app.state.owner).status(  # type: ignore[attr-defined]
                    self.launch_id, instance=self.instance_id
                ),
                name="instance-overview",
                group="instance-overview",
                thread=True,
                exclusive=True,
                exit_on_error=False,
            )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "instance-overview":
            return
        status = self.query_one("#instance-status", Label)
        if event.state.name == "ERROR":
            status.update(f"读取失败：{event.worker.error}")
        elif event.state.name == "SUCCESS":
            status.update("读取完成")
            log = self.query_one("#instance-result", RichLog)
            log.clear()
            log.write(Pretty(event.worker.result, expand_all=True))


class LaunchComponentsScreen(Screen[None]):
    TITLE = "Kairos"
    BINDINGS = [Binding("escape", "back", "返回"), Binding("r", "refresh", "刷新")]

    def __init__(self, launch_id: str, instance_id: str, mode: str) -> None:
        super().__init__()
        self.launch_id = launch_id
        self.instance_id = instance_id
        self.mode = mode
        self._components: dict[str, dict[str, Any]] = {}
        self.sub_title = f"首页 › 策略与运行 › {launch_id} › {instance_id} › 实例组件"

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label("实例组件", id="page-title")
        yield Label("正在读取组件…", id="components-status")
        yield DataTable(id="components-table", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#components-table", DataTable).add_columns(
            "组件", "状态", "PID", "Socket", "详情"
        )
        self.action_refresh()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self.run_worker(
            self._load,
            name="launch-components",
            group="launch-components",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _load(self) -> dict[str, dict[str, Any]]:
        owner = self.app.state.owner  # type: ignore[attr-defined]
        instance = owner.instance(self.mode, self.launch_id, self.instance_id)
        return LaunchRuntimeApplication(owner).component_status(instance)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "launch-components":
            return
        status = self.query_one("#components-status", Label)
        if event.state.name == "ERROR":
            status.update(f"读取失败：{event.worker.error}")
            return
        if event.state.name != "SUCCESS":
            return
        table = self.query_one("#components-table", DataTable)
        table.clear()
        values = event.worker.result or {}
        self._components = {str(name): dict(value) for name, value in values.items()}
        for component, value in values.items():
            table.add_row(
                component,
                str(value.get("status") or "unknown"),
                str(value.get("pid") or "—"),
                str(value.get("control_socket") or value.get("socket") or "—"),
                str(value.get("error") or value.get("detail") or "—"),
                key=component,
            )
        status.update("没有实例组件。" if not values else f"共 {len(values)} 个组件")
        if values:
            table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        component = str(event.row_key.value)
        if component == "market":
            self.app.push_screen(
                LaunchMarketComponentScreen(
                    self.launch_id, self.instance_id, self.mode
                )
            )
            return
        if component == "execution":
            self.app.push_screen(
                ExecutionComponentScreen(self.launch_id, self.instance_id, self.mode)
            )
            return
        value = self._components.get(component)
        if value is not None:
            self.app.push_screen(
                LaunchComponentResultScreen(
                    f"{self.launch_id}/{self.instance_id} · {component}", value
                )
            )


class LaunchComponentResultScreen(Screen[None]):
    TITLE = "Kairos"
    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self, title: str, value: Any) -> None:
        super().__init__()
        self.result_title = title
        self.value = value
        self.sub_title = f"首页 › 策略与运行 › {title}"

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label(self.result_title, id="page-title")
        yield RichLog(id="component-result", wrap=True, highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#component-result", RichLog).write(
            Pretty(self.value, expand_all=True)
        )

    def action_back(self) -> None:
        self.app.pop_screen()


class LaunchTimelineScreen(Screen[None]):
    TITLE = "Kairos"
    BINDINGS = [
        Binding("escape", "back", "返回"),
        Binding("r", "refresh", "刷新"),
        Binding("e", "export", "导出"),
    ]

    def __init__(self, launch_id: str, instance_id: str, mode: str) -> None:
        super().__init__()
        self.launch_id = launch_id
        self.instance_id = instance_id
        self.mode = mode
        self.sub_title = f"首页 › 策略与运行 › {launch_id} › {instance_id} › 时间线"

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label("实例时间线", id="page-title")
        yield Label("正在读取最近 200 条记录…", id="timeline-status")
        yield RichLog(id="timeline-result", wrap=True, highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        self.action_refresh()

    def action_back(self) -> None:
        self.app.pop_screen()

    def _application(self) -> LaunchInstanceTimelineApplication:
        owner = self.app.state.owner  # type: ignore[attr-defined]
        return LaunchInstanceTimelineApplication(
            owner.instance(self.mode, self.launch_id, self.instance_id)
        )

    def action_refresh(self) -> None:
        self.run_worker(
            lambda: self._application().list(limit=200),
            name="timeline-list",
            group="timeline-action",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def action_export(self) -> None:
        default = f"{self.launch_id}-{self.instance_id}-timeline.jsonl"
        self.app.push_screen(
            InputDialog("导出文件", value=default),
            self._export_to,
        )

    def _export_to(self, destination: str | None) -> None:
        if not destination:
            return
        state = self.app.state  # type: ignore[attr-defined]
        if state.dry_run or state.no_exec:
            self.query_one("#timeline-status", Label).update(
                f"预览：导出到 {destination}"
            )
            return
        self.run_worker(
            lambda: self._application().export(destination),
            name="timeline-export",
            group="timeline-action",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "timeline-action":
            return
        status = self.query_one("#timeline-status", Label)
        if event.state.name == "ERROR":
            status.update(f"操作失败：{event.worker.error}")
        elif event.state.name == "SUCCESS":
            if event.worker.name == "timeline-export":
                status.update(f"已导出：{event.worker.result}")
            else:
                records = event.worker.result or []
                status.update(f"共 {len(records)} 条记录")
                log = self.query_one("#timeline-result", RichLog)
                log.clear()
                for record in records:
                    log.write(Pretty(record, expand_all=True))
