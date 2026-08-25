"""Workspace and system actions for the Operations Workbench slice."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kairospy.system.apps.components.application import ComponentProcessApplication
from kairospy.system.apps.configuration.application import ConfigApplication
from kairospy.system.apps.workspace.application import WorkspaceApplication
from kairospy.system.apps.workspace_services import WorkspaceServiceApplication

from ....widgets import ActionItem


PROJECT_ACTIONS = (
    ActionItem("status", "项目概览", "查看项目身份、路径和 Workspace 位置", "1"),
    ActionItem("doctor", "检查项目", "检查目录、配置和必要资源", "2"),
    ActionItem("open", "切换项目", "打开另一个已有项目", "3"),
    ActionItem("init", "创建项目", "创建空项目或从模板创建", "4"),
    ActionItem("scaffold", "安装模板", "为当前项目安装 backtest 示例", "5"),
)

PROJECT_START_ACTIONS = (
    ActionItem("open", "打开项目", "选择一个已有项目", "1"),
    ActionItem("init", "创建项目", "创建空项目或从模板创建", "2"),
)


def project_actions(*, has_project: bool) -> tuple[ActionItem, ...]:
    """Expose only project actions valid for the current shell state."""

    return PROJECT_ACTIONS if has_project else PROJECT_START_ACTIONS


CONFIG_ACTIONS = (
    ActionItem("paths", "查看路径", "配置、状态、运行和日志目录", "1"),
    ActionItem("manifest", "查看 Manifest", "读取 Workspace manifest", "2"),
    ActionItem("show", "查看全部配置", "读取并安全遮盖 TOML 配置", "3"),
    ActionItem("doctor", "运行配置诊断", "检查目录、Launch 与依赖资源", "4"),
    ActionItem("explain", "解释指定配置", "按名称查看配置路径和值", "5"),
    ActionItem("operations", "查看可用操作", "列出配置 Application 支持的操作", "6"),
    ActionItem("profiles", "管理 Profiles", "列出、创建并切换 Profile", "7"),
    ActionItem("models", "管理模型连接", "进入模型连接资源上下文", "8"),
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
        if self.action == "open" and "root" not in self.values:
            return (
                "root",
                "项目目录",
                "请输入已有项目目录；输入 /back 取消。",
            )
        if self.action == "open":
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
    if prompt.action == "open":
        owner = WorkspaceApplication().open(prompt.values["root"])
        state.owner = owner
        state.workspace_arg = Path(owner.paths.root)
        state.refresh_snapshot()
        return {
            "status": "opened",
            "workspace_id": owner.workspace_id,
            "project_root": str(owner.paths.project_root),
            "workspace_root": str(owner.paths.root),
        }
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
        return application.list_status()[component]
    if action == "start":
        return {
            "component": component,
            **WorkspaceServiceApplication(owner).start_and_keep_running(component),
        }
    if action in {"stop", "restart"}:
        if action == "stop":
            result = WorkspaceServiceApplication(owner).stop(component)
        else:
            result = WorkspaceServiceApplication(owner).restart(component)
        return {"component": component, **result}
    if action in {"logs", "log-tail"}:
        return application.log_snapshot(
            component, limit=500 if action == "log-tail" else 200
        )
    if action == "diagnostics":
        return application.doctor()["components"][component]
    if action == "repair":
        result = WorkspaceServiceApplication(owner).repair(component, start=False)
        return {"component": component, **result}
    if action == "repair-start":
        result = WorkspaceServiceApplication(owner).repair(component, start=True)
        return {"component": component, **result}
    raise ValueError(f"unknown service action: {action}")


def _owner(state: Any) -> Any:
    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的 workspace")
    return state.owner


__all__ = [
    "BUSINESS_ACTIONS",
    "CONFIG_ACTIONS",
    "PROJECT_ACTIONS",
    "PROJECT_START_ACTIONS",
    "PROFILE_ACTIONS",
    "ProjectPromptState",
    "project_actions",
    "execute_config",
    "execute_operation",
    "execute_project",
    "execute_project_write",
    "execute_service",
    "list_services",
    "mutate_profile",
]
