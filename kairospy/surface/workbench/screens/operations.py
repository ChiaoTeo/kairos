"""Workspace and system operations screens."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.pretty import Pretty
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Label, OptionList, RichLog
from textual.worker import Worker

from kairospy.system.apps.configuration.application import (
    ConfigApplication,
    ConfigurationMigrationApplication,
)
from kairospy.system.apps.launch.application import (
    WorkspaceComponentDependencyApplication,
)
from kairospy.system.apps.components.application import ComponentProcessApplication
from kairospy.system.apps.workspace.application import WorkspaceApplication

from ..dialogs import ConfirmDialog, InputDialog, SelectDialog, SelectOption
from ..widgets import ActionItem, ActionList, WorkspaceHeader
from .observe import ObserveScreen
from .resources import ResourceListScreen


OPERATIONS_ACTIONS = (
    ActionItem("project", "项目工作区", "创建、检查并安装项目模板", "1"),
    ActionItem("observe", "实时观测", "组件、Launch 与市场状态总览", "2"),
    ActionItem("services", "系统服务", "管理 Reference 与 Market 进程", "3"),
    ActionItem("doctor", "诊断系统", "检查 socket、健康文件与进程锁", "4"),
    ActionItem("repair", "修复 stale 资源", "仅清理可证明已失效的运行资源", "5"),
    ActionItem("config", "高级配置", "路径、配置、Profile 与模型连接", "6"),
    ActionItem("migration", "配置升级", "查看旧格式配置及安全迁移要求", "7"),
    ActionItem("workspace", "Workspace 信息", "查看当前工作区路径和身份", "8"),
    ActionItem("business", "业务工具", "Risk、Capital 与 Provider 集成", "9"),
)


class OperationsScreen(Screen[None]):
    TITLE = "Kairos"
    SUB_TITLE = "首页 › 系统维护"
    BINDINGS = [Binding("escape", "back", "返回")]

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label("维护系统", id="page-title")
        yield ActionList(*OPERATIONS_ACTIONS, id="operations-actions")
        yield Label("选择维护操作。", id="operations-status")
        yield RichLog(id="operations-result", wrap=True, highlight=False)
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        action = event.option.id
        if action == "project":
            self.app.push_screen(ProjectScreen())
        elif action == "observe":
            self.app.push_screen(ObserveScreen())
        elif action == "services":
            self.app.push_screen(ServicesScreen())
        elif action == "config":
            self.app.push_screen(AdvancedConfigScreen())
        elif action == "business":
            from .business_tools import BusinessToolsScreen

            self.app.push_screen(BusinessToolsScreen())
        elif action == "repair":
            if self.app.state.yes:  # type: ignore[attr-defined]
                self._run(action)
                return
            self.app.push_screen(
                ConfirmDialog(
                    "修复 stale 运行资源",
                    "只会删除没有存活进程或锁所有者的 stale socket/health 文件。",
                    confirm_label="修复",
                ),
                lambda confirmed: self._run(action) if confirmed else None,
            )
        elif action is not None:
            self._run(action)

    def _run(self, action: str) -> None:
        state = self.app.state  # type: ignore[attr-defined]
        if action == "repair" and (state.dry_run or state.no_exec):
            self._show({"status": "preview", "action": "repair"})
            return
        self.query_one("#operations-status", Label).update(f"正在执行：{action}…")
        self.run_worker(
            lambda: self._execute(action),
            name=f"operations-{action}",
            group="operations-action",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _execute(self, action: str) -> Any:
        state = self.app.state  # type: ignore[attr-defined]
        owner = state.owner
        if owner is None:
            raise RuntimeError(state.load_error or "当前没有可用的 workspace")
        if action == "doctor":
            return ComponentProcessApplication(owner).doctor()
        if action == "repair":
            return ComponentProcessApplication(owner).repair()
        if action == "migration":
            return ConfigurationMigrationApplication(owner).preview()
        if action == "workspace":
            return {
                "workspace_id": owner.workspace_id,
                "project_root": str(owner.paths.project_root),
                "workspace_root": str(owner.paths.root),
                "manifest": str(owner.paths.manifest),
            }
        raise RuntimeError(f"unknown operations action: {action}")

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "operations-action":
            return
        if event.state.name == "ERROR":
            self.query_one("#operations-status", Label).update(
                f"操作失败：{event.worker.error}"
            )
        elif event.state.name == "SUCCESS":
            self.query_one("#operations-status", Label).update("操作完成")
            self._show(event.worker.result)

    def _show(self, value: Any) -> None:
        log = self.query_one("#operations-result", RichLog)
        log.clear()
        log.write(Pretty(value, expand_all=True))


PROJECT_ACTIONS = (
    ActionItem("status", "查看项目状态", "Workspace 身份与路径", "1"),
    ActionItem("init", "创建项目", "初始化项目并可安装 backtest 示例", "2"),
    ActionItem("scaffold", "安装示例模板", "在当前项目安装可运行的 backtest 示例", "3"),
    ActionItem("doctor", "运行项目诊断", "检查 Launch 配置与运行准备", "4"),
)


class ProjectScreen(Screen[None]):
    """Project lifecycle without routing back through Typer commands."""

    TITLE = "Kairos"
    SUB_TITLE = "首页 › 系统维护 › 项目工作区"
    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self) -> None:
        super().__init__()
        self._init_root = ""
        self._init_id = ""

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label("项目工作区", id="page-title")
        yield ActionList(*PROJECT_ACTIONS, id="project-actions")
        yield Label("选择项目操作。", id="project-status")
        yield RichLog(id="project-result", wrap=True, highlight=False)
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        action = event.option.id
        if action == "init":
            self.app.push_screen(
                InputDialog("项目目录", placeholder="my-project"), self._set_init_root
            )
        elif action == "scaffold":
            if self.app.state.yes:  # type: ignore[attr-defined]
                self._run("scaffold")
                return
            self.app.push_screen(
                ConfirmDialog(
                    "安装 backtest 示例模板",
                    "将在当前项目创建示例 Launch 和策略文件，不会覆盖已有文件。",
                    confirm_label="安装",
                ),
                lambda confirmed: self._run("scaffold") if confirmed else None,
            )
        elif action is not None:
            self._run(action)

    def _set_init_root(self, value: str | None) -> None:
        if not value:
            return
        self._init_root = value
        default_id = Path(value).expanduser().name or "my-project"
        self.app.push_screen(
            InputDialog("项目名 / Workspace ID", value=default_id),
            self._set_init_id,
        )

    def _set_init_id(self, value: str | None) -> None:
        if not value:
            return
        self._init_id = value
        self.app.push_screen(
            SelectDialog(
                "选择项目模板",
                (
                    SelectOption("backtest", "backtest · 安装离线可运行示例"),
                    SelectOption("none", "不安装模板"),
                ),
            ),
            self._confirm_init,
        )

    def _confirm_init(self, template: str | None) -> None:
        if template is None:
            return
        template_label = "backtest" if template == "backtest" else "不安装"
        if self.app.state.yes:  # type: ignore[attr-defined]
            self._run("init", template=template)
            return
        self.app.push_screen(
            ConfirmDialog(
                "创建 Kairos 项目",
                f"目录：{self._init_root}\nWorkspace：{self._init_id}\n模板：{template_label}",
                confirm_label="创建",
            ),
            lambda confirmed: (
                self._run("init", template=template) if confirmed else None
            ),
        )

    def _run(self, action: str, *, template: str | None = None) -> None:
        state = self.app.state  # type: ignore[attr-defined]
        if action in {"init", "scaffold"} and (state.dry_run or state.no_exec):
            self._show(
                {
                    "status": "preview",
                    "action": action,
                    "root": self._init_root or None,
                    "workspace_id": self._init_id or None,
                    "template": template or "backtest",
                }
            )
            return
        self.query_one("#project-status", Label).update(f"正在执行：{action}…")
        self.run_worker(
            lambda: self._execute(action, template=template),
            name=f"project-{action}",
            group="project-action",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _execute(self, action: str, *, template: str | None) -> Any:
        state = self.app.state  # type: ignore[attr-defined]
        if action == "init":
            selected_template = None if template == "none" else template
            owner = WorkspaceApplication().init_project(
                self._init_root,
                workspace_id=self._init_id,
                template=selected_template,
            )
            state.owner = owner
            state.workspace_arg = Path(owner.paths.root)
            state.refresh_snapshot()
            return {
                "status": "initialized",
                "workspace_id": owner.workspace_id,
                "project_root": str(owner.paths.project_root),
                "workspace_root": str(owner.paths.root),
                "template": selected_template,
            }
        owner = state.owner
        if owner is None:
            raise RuntimeError(state.load_error or "当前没有可用的 workspace")
        if action == "status":
            return {
                "workspace_id": owner.workspace_id,
                "project_root": str(owner.paths.project_root),
                "workspace_root": str(owner.paths.root),
            }
        if action == "scaffold":
            created = WorkspaceApplication().install_template(
                owner, template="backtest"
            )
            return {
                "status": "scaffolded",
                "template": "backtest",
                "created": [str(path) for path in created],
            }
        if action == "doctor":
            return ConfigApplication(owner).doctor()
        raise RuntimeError(f"unknown project action: {action}")

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "project-action":
            return
        status = self.query_one("#project-status", Label)
        if event.state.name == "ERROR":
            status.update(f"操作失败：{event.worker.error}")
        elif event.state.name == "SUCCESS":
            status.update("操作完成")
            self._show(event.worker.result)

    def _show(self, value: Any) -> None:
        log = self.query_one("#project-result", RichLog)
        log.clear()
        log.write(Pretty(value, expand_all=True))


CONFIG_ACTIONS = (
    ActionItem("paths", "查看路径", "Workspace 的配置、状态、运行和日志目录", "1"),
    ActionItem("manifest", "查看 Manifest", "读取 Workspace manifest", "2"),
    ActionItem("show", "查看全部配置", "读取并安全遮盖 TOML 配置", "3"),
    ActionItem("doctor", "运行配置诊断", "检查目录、Launch 与依赖资源", "4"),
    ActionItem("explain", "解释指定配置", "按名称查看配置路径和值", "5"),
    ActionItem("operations", "查看可用操作", "列出配置 Application 支持的操作", "6"),
    ActionItem("profiles", "管理 Profiles", "列出、创建并切换配置 Profile", "7"),
    ActionItem("models", "管理 AI 模型连接", "查看、配置并测试模型资源", "8"),
)


class AdvancedConfigScreen(Screen[None]):
    TITLE = "Kairos"
    SUB_TITLE = "首页 › 系统维护 › 高级配置"
    BINDINGS = [Binding("escape", "back", "返回")]

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label("高级配置", id="page-title")
        yield ActionList(*CONFIG_ACTIONS, id="config-actions")
        yield Label("配置值中的密钥会自动遮盖。", id="config-status")
        yield RichLog(id="config-result", wrap=True, highlight=False)
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        action = event.option.id
        if action == "explain":
            self.app.push_screen(
                InputDialog("配置名称", placeholder="launches/demo-backtest"),
                lambda value: self._run("explain", value) if value else None,
            )
        elif action == "profiles":
            self.app.push_screen(ProfileScreen())
        elif action == "models":
            self.app.push_screen(ResourceListScreen("models"))
        elif action is not None:
            self._run(action)

    def _run(self, action: str, name: str | None = None) -> None:
        self.query_one("#config-status", Label).update(f"正在读取：{action}…")
        self.run_worker(
            lambda: self._execute(action, name),
            name=f"config-{action}",
            group="config-action",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _execute(self, action: str, name: str | None) -> Any:
        state = self.app.state  # type: ignore[attr-defined]
        if state.owner is None:
            raise RuntimeError(state.load_error or "当前没有可用的 workspace")
        application = ConfigApplication(state.owner)
        if action == "paths":
            return application.paths()
        if action == "manifest":
            return application.manifest()
        if action == "show":
            return application.show()
        if action == "doctor":
            return application.doctor()
        if action == "explain" and name is not None:
            return application.explain(name)
        if action == "operations":
            return application.operations()
        raise RuntimeError(f"unknown config action: {action}")

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "config-action":
            return
        status = self.query_one("#config-status", Label)
        if event.state.name == "ERROR":
            status.update(f"读取失败：{event.worker.error}")
        elif event.state.name == "SUCCESS":
            status.update("读取完成")
            log = self.query_one("#config-result", RichLog)
            log.clear()
            log.write(Pretty(event.worker.result, expand_all=True))


class ProfileScreen(Screen[None]):
    TITLE = "Kairos"
    SUB_TITLE = "首页 › 系统维护 › 高级配置 › Profiles"
    BINDINGS = [
        Binding("escape", "back", "返回"),
        Binding("r", "refresh", "刷新"),
        Binding("n", "create", "新建"),
    ]

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label("配置 Profiles", id="page-title")
        yield Label("Enter 切换到选中的 Profile；N 新建。", id="profiles-status")
        yield DataTable(id="profiles-table", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#profiles-table", DataTable).add_columns("Profile", "状态")
        self.action_refresh()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self.run_worker(
            lambda: ConfigApplication(self.app.state.owner).profiles(),  # type: ignore[attr-defined]
            name="profiles-list",
            group="profiles-list",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def action_create(self) -> None:
        self.app.push_screen(
            InputDialog("新 Profile 名称", placeholder="paper"),
            self._confirm_create,
        )

    def _confirm_create(self, name: str | None) -> None:
        if not name:
            return
        if self.app.state.yes:  # type: ignore[attr-defined]
            self._mutate("create", name)
            return
        self.app.push_screen(
            ConfirmDialog(
                f"创建 Profile {name}",
                "将在当前 Workspace 写入新的配置 Profile。",
                confirm_label="创建",
            ),
            lambda confirmed: self._mutate("create", name) if confirmed else None,
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        name = str(event.row_key.value)
        if self.app.state.yes:  # type: ignore[attr-defined]
            self._mutate("use", name)
            return
        self.app.push_screen(
            ConfirmDialog(
                f"切换到 Profile {name}",
                "后续配置读取将使用该 Profile。",
                confirm_label="切换",
            ),
            lambda confirmed: self._mutate("use", name) if confirmed else None,
        )

    def _mutate(self, action: str, name: str) -> None:
        state = self.app.state  # type: ignore[attr-defined]
        if state.dry_run or state.no_exec:
            self.query_one("#profiles-status", Label).update(
                f"预览：{action} profile {name}"
            )
            return
        self.run_worker(
            lambda: self._execute_mutation(action, name),
            name=f"profile-{action}",
            group="profile-mutation",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _execute_mutation(self, action: str, name: str) -> str:
        application = ConfigApplication(self.app.state.owner)  # type: ignore[attr-defined]
        path = (
            application.create_profile(name)
            if action == "create"
            else application.use_profile(name)
        )
        return str(path)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group == "profiles-list":
            if event.state.name == "ERROR":
                self.query_one("#profiles-status", Label).update(
                    f"读取失败：{event.worker.error}"
                )
            elif event.state.name == "SUCCESS":
                table = self.query_one("#profiles-table", DataTable)
                table.clear()
                for name in event.worker.result or ():
                    table.add_row(name, "可用", key=name)
                self.query_one("#profiles-status", Label).update(
                    f"共 {table.row_count} 个 Profile"
                )
                table.focus()
        elif event.worker.group == "profile-mutation":
            if event.state.name == "ERROR":
                self.query_one("#profiles-status", Label).update(
                    f"操作失败：{event.worker.error}"
                )
            elif event.state.name == "SUCCESS":
                self.query_one("#profiles-status", Label).update(
                    f"操作完成：{event.worker.result}"
                )
                self.action_refresh()


class ServicesScreen(Screen[None]):
    TITLE = "Kairos"
    SUB_TITLE = "首页 › 系统维护 › 系统服务"
    BINDINGS = [Binding("escape", "back", "返回"), Binding("r", "refresh", "刷新")]

    def __init__(self) -> None:
        super().__init__()
        self._statuses: dict[str, dict[str, Any]] = {}

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label("系统服务", id="page-title")
        yield Label("正在读取状态…", id="services-status")
        yield DataTable(id="services-table", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#services-table", DataTable).add_columns(
            "组件", "状态", "PID", "Socket", "详情"
        )
        self.action_refresh()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self.run_worker(
            self._load,
            name="services-list",
            group="services-list",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _load(self) -> dict[str, dict[str, Any]]:
        return ComponentProcessApplication(self.app.state.owner).list_status()  # type: ignore[attr-defined,no-any-return]

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "services-list":
            return
        status = self.query_one("#services-status", Label)
        if event.state.name == "ERROR":
            status.update(f"读取失败：{event.worker.error}")
            return
        if event.state.name != "SUCCESS":
            return
        self._statuses = event.worker.result or {}
        table = self.query_one("#services-table", DataTable)
        table.clear()
        for component, value in self._statuses.items():
            table.add_row(
                component,
                str(value.get("status") or "unknown"),
                str(value.get("pid") or "—"),
                str(value.get("control_socket") or "—"),
                str(value.get("error") or value.get("detail") or "—"),
                key=component,
            )
        status.update(f"共 {len(self._statuses)} 个 Workspace 服务")
        table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        component = str(event.row_key.value)
        if component in self._statuses:
            self.app.push_screen(ServiceDetailScreen(component))


SERVICE_ACTIONS = (
    ActionItem("status", "查看状态", "读取组件健康与运行资源", "1"),
    ActionItem("start", "启动", "启动组件并等待就绪", "2"),
    ActionItem("stop", "停止", "请求组件安全停止", "3"),
    ActionItem("restart", "重启", "停止后启动新的组件进程", "4"),
    ActionItem("logs", "查看日志", "读取最近 200 行进程日志", "5"),
)


class ServiceDetailScreen(Screen[None]):
    TITLE = "Kairos"
    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self, component: str) -> None:
        super().__init__()
        self.component = component
        self.sub_title = f"首页 › 系统维护 › 系统服务 › {component}"

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label(self.component, id="page-title")
        yield ActionList(*SERVICE_ACTIONS, id="service-actions")
        yield Label("选择服务操作。", id="service-status")
        yield RichLog(id="service-result", wrap=True, highlight=False)
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        action = event.option.id
        if action in {"start", "stop", "restart"}:
            if self.app.state.yes:  # type: ignore[attr-defined]
                self._run(action)
                return
            self.app.push_screen(
                ConfirmDialog(
                    f"{action} {self.component}",
                    "该操作会改变 Workspace 服务状态。",
                    confirm_label="继续",
                ),
                lambda confirmed: self._run(action) if confirmed else None,
            )
        elif action is not None:
            self._run(action)

    def _run(self, action: str) -> None:
        state = self.app.state  # type: ignore[attr-defined]
        if action in {"start", "stop", "restart"} and (state.dry_run or state.no_exec):
            self._show(
                {"status": "preview", "action": action, "component": self.component}
            )
            return
        self.query_one("#service-status", Label).update(f"正在执行：{action}…")
        self.run_worker(
            lambda: self._execute(action),
            name=f"service-{action}",
            group="service-action",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _execute(self, action: str) -> Any:
        owner = self.app.state.owner  # type: ignore[attr-defined]
        app = ComponentProcessApplication(owner)
        if action == "status":
            return app.status(self.component)
        if action == "start":
            return app.ensure_running(self.component).status()
        if action == "stop":
            WorkspaceComponentDependencyApplication(owner).require_clear(
                self.component, "stop"
            )
            return app.stop(self.component)
        if action == "restart":
            WorkspaceComponentDependencyApplication(owner).require_clear(
                self.component, "restart"
            )
            return app.restart(self.component).status()
        if action == "logs":
            return {
                "component": self.component,
                "lines": list(app.logs(self.component)),
            }
        raise RuntimeError(f"unknown service action: {action}")

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "service-action":
            return
        if event.state.name == "ERROR":
            self.query_one("#service-status", Label).update(
                f"操作失败：{event.worker.error}"
            )
        elif event.state.name == "SUCCESS":
            self.query_one("#service-status", Label).update("操作完成")
            self._show(event.worker.result)

    def _show(self, value: Any) -> None:
        log = self.query_one("#service-result", RichLog)
        log.clear()
        log.write(Pretty(value, expand_all=True))
