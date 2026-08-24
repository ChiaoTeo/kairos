"""Single-input Workspace and system operation helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kairospy.system.apps.components.application import ComponentProcessApplication
from kairospy.system.apps.configuration.application import (
    ConfigApplication,
    ConfigurationMigrationApplication,
)
from kairospy.system.apps.launch.application import (
    WorkspaceComponentDependencyApplication,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication

from ...widgets import ActionItem


PROJECT_ACTIONS = (
    ActionItem("status", "查看项目状态", "Workspace 身份与路径", "1"),
    ActionItem("init", "创建项目", "逐步输入目录、Workspace ID 与模板", "2"),
    ActionItem("scaffold", "安装示例模板", "安装可运行的 backtest 示例", "3"),
    ActionItem("doctor", "运行项目诊断", "检查 Launch 配置与运行准备", "4"),
)

CONFIG_ACTIONS = (
    ActionItem("paths", "查看路径", "配置、状态、运行和日志目录", "1"),
    ActionItem("manifest", "查看 Manifest", "读取 Workspace manifest", "2"),
    ActionItem("show", "查看全部配置", "读取并安全遮盖 TOML 配置", "3"),
    ActionItem("doctor", "运行配置诊断", "检查目录、Launch 与依赖资源", "4"),
    ActionItem("explain", "解释指定配置", "按名称查看配置路径和值", "5"),
    ActionItem("operations", "查看可用操作", "列出配置 Application 支持的操作", "6"),
    ActionItem("profiles", "管理 Profiles", "列出、创建并切换 Profile", "7"),
    ActionItem("models", "管理 AI 模型连接", "进入模型资源上下文", "8"),
)

SERVICE_ACTIONS = (
    ActionItem("status", "查看状态", "读取组件健康与运行资源", "1"),
    ActionItem("start", "启动", "启动组件并等待就绪", "2"),
    ActionItem("stop", "停止", "请求组件安全停止", "3"),
    ActionItem("restart", "重启", "停止后启动新的组件进程", "4"),
    ActionItem("logs", "查看日志", "读取最近 200 行进程日志", "5"),
)

BUSINESS_ACTIONS = (
    ActionItem("risk", "Risk 工具", "schema、doctor、preview 与连接态查询", "1"),
    ActionItem("capital", "Capital 工具", "schema、doctor、preview 与 plan", "2"),
    ActionItem("integration", "Provider 集成", "能力、转账与 Earn 说明", "3"),
)

PROFILE_ACTIONS = (
    ActionItem("list", "列出 Profiles", "查看当前 Workspace 的配置 Profiles", "1"),
    ActionItem("create", "创建 Profile", "写入新的配置 Profile", "2"),
    ActionItem("use", "切换 Profile", "后续配置读取使用指定 Profile", "3"),
)


@dataclass(slots=True)
class ProjectPromptState:
    """Staged values for project writes in the shared bottom input."""

    action: str
    values: dict[str, str] = field(default_factory=dict)

    @property
    def dangerous(self) -> bool:
        return True

    def next_prompt(self) -> tuple[str, str, str] | None:
        if self.action == "scaffold":
            return None
        for name, label, detail in (
            ("root", "项目目录", "例如 my-project；输入 /back 取消。"),
            (
                "workspace_id",
                "项目名 / Workspace ID",
                "直接回车使用项目目录名；输入 /back 取消。",
            ),
            (
                "template",
                "项目模板（backtest / none）",
                "直接回车使用 backtest；输入 /back 取消。",
            ),
        ):
            if name not in self.values:
                return name, label, detail
        return None

    def accept(self, name: str, value: str) -> None:
        actual = value.strip()
        if name == "root":
            if not actual:
                raise ValueError("项目目录不能为空。")
            self.values[name] = actual
            return
        if name == "workspace_id":
            self.values[name] = actual or Path(self.values["root"]).expanduser().name
            return
        actual = actual or "backtest"
        if actual not in {"backtest", "none"}:
            raise ValueError("项目模板必须是 backtest 或 none。")
        self.values[name] = actual

    def summary(self) -> dict[str, Any]:
        return {"action": self.action, **self.values}


def execute_operation(state: Any, action: str) -> Any:
    owner = _owner(state)
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
    raise ValueError(f"unknown operation: {action}")


def execute_project(state: Any, action: str) -> Any:
    owner = _owner(state)
    if action == "status":
        return {
            "workspace_id": owner.workspace_id,
            "project_root": str(owner.paths.project_root),
            "workspace_root": str(owner.paths.root),
        }
    if action == "doctor":
        return ConfigApplication(owner).doctor()
    raise ValueError(f"project action requires a guided write flow: {action}")


def execute_project_write(state: Any, prompt: ProjectPromptState) -> Any:
    if prompt.action == "scaffold":
        owner = _owner(state)
        created = WorkspaceApplication().install_template(owner, template="backtest")
        return {
            "status": "scaffolded",
            "template": "backtest",
            "created": [str(path) for path in created],
        }
    template = prompt.values["template"]
    owner = WorkspaceApplication().init_project(
        prompt.values["root"],
        workspace_id=prompt.values["workspace_id"],
        template=None if template == "none" else template,
    )
    state.owner = owner
    state.workspace_arg = Path(owner.paths.root)
    state.refresh_snapshot()
    return {
        "status": "initialized",
        "workspace_id": owner.workspace_id,
        "project_root": str(owner.paths.project_root),
        "workspace_root": str(owner.paths.root),
        "template": None if template == "none" else template,
    }


def mutate_profile(state: Any, action: str, name: str) -> Any:
    application = ConfigApplication(_owner(state))
    if action == "create":
        return {"action": action, "path": str(application.create_profile(name))}
    if action == "use":
        return {"action": action, "path": str(application.use_profile(name))}
    raise ValueError(f"unknown profile action: {action}")


def execute_config(state: Any, action: str, name: str | None = None) -> Any:
    application = ConfigApplication(_owner(state))
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
    if action == "profiles":
        return application.profiles()
    raise ValueError(f"unknown config action: {action}")


def list_services(state: Any) -> tuple[dict[str, Any], ...]:
    values = ComponentProcessApplication(_owner(state)).list_status()
    return tuple(
        {"component": component, **dict(status)}
        for component, status in sorted(values.items())
    )


def execute_service(state: Any, component: str, action: str) -> Any:
    owner = _owner(state)
    application = ComponentProcessApplication(owner)
    if action == "status":
        return application.status(component)
    if action == "start":
        return application.ensure_running(component).status()
    if action in {"stop", "restart"}:
        WorkspaceComponentDependencyApplication(owner).require_clear(component, action)
        return (
            application.stop(component)
            if action == "stop"
            else application.restart(component).status()
        )
    if action == "logs":
        return {"component": component, "lines": list(application.logs(component))}
    raise ValueError(f"unknown service action: {action}")


def _owner(state: Any) -> Any:
    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的 workspace")
    return state.owner


__all__ = [
    "BUSINESS_ACTIONS",
    "CONFIG_ACTIONS",
    "PROJECT_ACTIONS",
    "PROFILE_ACTIONS",
    "ProjectPromptState",
    "SERVICE_ACTIONS",
    "execute_config",
    "execute_operation",
    "execute_project",
    "execute_project_write",
    "execute_service",
    "list_services",
    "mutate_profile",
]
