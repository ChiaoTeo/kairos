"""Risk, Capital, and Integration actions for Operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kairospy.system.apps.components.application import (
    CapitalSystemClient,
    NativeCliApplication,
    RiskSystemClient,
)

from ....widgets import ActionItem


RISK_ACTIONS = (
    ActionItem("schema", "查看 Schema", "查看 Risk 请求结构", "1"),
    ActionItem("doctor", "检查请求文件", "校验 policy 或 authorization 文件", "2"),
    ActionItem("preview", "本地预演", "用 policy 文件评估 Risk request", "3"),
    ActionItem("health", "运行状态", "连接 Workspace Risk 服务", "4"),
    ActionItem("limits", "限额使用", "读取指定 Risk actor 的限额", "5"),
    ActionItem("reservations", "活动预留", "读取指定 Risk actor 的预留", "6"),
)

CAPITAL_ACTIONS = (
    ActionItem("schema", "查看 Schema", "查看 Capital 请求结构", "1"),
    ActionItem("doctor", "检查请求文件", "校验 Capital 请求", "2"),
    ActionItem("preview", "本地预览", "读取并总结 Capital 请求", "3"),
    ActionItem("plan", "生成计划", "根据目标、需求和可用性生成计划", "4"),
    ActionItem("health", "运行状态", "连接 Workspace Capital 服务", "5"),
    ActionItem("current", "当前状态", "读取 Capital group 当前视图", "6"),
)

INTEGRATION_ACTIONS = (
    ActionItem("capabilities", "Provider 集成", "认证、连接与标准化外部事实", "1"),
    ActionItem("transfer", "Transfer", "Provider 资产划转能力说明", "2"),
    ActionItem("earn", "Earn", "Provider 理财产品与申赎能力说明", "3"),
)


@dataclass(slots=True)
class BusinessPromptState:
    tool: str
    action: str
    values: dict[str, str] = field(default_factory=dict)

    def next_prompt(self) -> tuple[str, str, str] | None:
        for name, label, default in _PROMPTS.get((self.tool, self.action), ()):
            if name not in self.values:
                return name, label, f"直接回车使用 {default}；输入 /back 取消。"
        return None

    def accept(self, name: str, raw: str) -> None:
        default = next(
            default
            for field_name, _label, default in _PROMPTS[(self.tool, self.action)]
            if field_name == name
        )
        value = raw.strip() or default
        if name == "kind":
            allowed = (
                {"all", "policy", "authorization"}
                if self.tool == "risk"
                else {"all", "funding-objective", "capital-demand", "availability"}
            )
            if value not in allowed:
                raise ValueError("请求类型不受支持：" + value)
        if not value:
            raise ValueError(f"{name} 不能为空")
        self.values[name] = value


def execute(state: Any, prompt: BusinessPromptState) -> Any:
    owner = _owner(state)
    tool, action, values = prompt.tool, prompt.action, prompt.values
    if tool == "risk":
        if action in {"schema", "doctor", "preview"}:
            arguments = ["standalone", action]
            if action == "schema" and values.get("kind") != "all":
                arguments.append(values["kind"])
            elif action == "doctor":
                arguments.extend(("--kind", values["kind"], "--file", values["file"]))
            elif action == "preview":
                arguments.extend(
                    (
                        "--policy-file",
                        values["policy"],
                        "--request-file",
                        values["request"],
                    )
                )
            return NativeCliApplication(owner).run("risk", arguments)
        socket = owner.paths.process_socket("risk")
        if not socket.exists():
            raise RuntimeError("Workspace Risk 服务尚未运行")
        client = RiskSystemClient(socket, view_root=owner.paths.snapshots, timeout=30.0)
        if action == "health":
            return client.health()
        if action == "limits":
            return client.latest_limits(actor_id=values["actor"])
        return client.latest_reservations(actor_id=values["actor"])
    if tool == "capital":
        if action in {"schema", "doctor", "preview", "plan"}:
            arguments = ["standalone", action]
            if action == "schema" and values.get("kind") != "all":
                arguments.append(values["kind"])
            elif action in {"doctor", "preview"}:
                arguments.extend(("--kind", values["kind"], "--file", values["file"]))
            elif action == "plan":
                arguments.extend(
                    (
                        "--objective-file",
                        values["objective"],
                        "--demand-file",
                        values["demand"],
                        "--availability-file",
                        values["availability"],
                    )
                )
            return NativeCliApplication(owner).run("capital", arguments)
        socket = owner.paths.process_socket("capital")
        if not socket.exists():
            raise RuntimeError("Workspace Capital 服务尚未运行")
        client = CapitalSystemClient(
            socket, view_root=owner.paths.snapshots, timeout=30.0
        )
        if action == "health":
            return client.health()
        return client.current_metadata(values["group"])
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
    return details[action]


def actions(tool: str) -> tuple[ActionItem, ...]:
    return {
        "risk": RISK_ACTIONS,
        "capital": CAPITAL_ACTIONS,
        "integration": INTEGRATION_ACTIONS,
    }[tool]


def _owner(state: Any) -> Any:
    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的 Workspace")
    return state.owner


_PROMPTS = {
    ("risk", "schema"): (("kind", "Schema 类型", "all"),),
    ("risk", "doctor"): (
        ("kind", "请求类型（policy / authorization）", "policy"),
        ("file", "请求文件", "risk-policy.json"),
    ),
    ("risk", "preview"): (
        ("policy", "Risk policy 文件", "risk-policy.json"),
        ("request", "Risk request 文件", "risk-request.json"),
    ),
    ("risk", "limits"): (("actor", "Risk actor ID", "risk"),),
    ("risk", "reservations"): (("actor", "Risk actor ID", "risk"),),
    ("capital", "schema"): (("kind", "Schema 类型", "all"),),
    ("capital", "doctor"): (
        ("kind", "Capital request kind", "funding-objective"),
        ("file", "Capital request 文件", "capital-request.json"),
    ),
    ("capital", "preview"): (
        ("kind", "Capital request kind", "funding-objective"),
        ("file", "Capital request 文件", "capital-request.json"),
    ),
    ("capital", "plan"): (
        ("objective", "Funding objective 文件", "funding-objective.json"),
        ("demand", "Capital demand 文件", "capital-demand.json"),
        ("availability", "Availability 文件", "availability.json"),
    ),
    ("capital", "current"): (("group", "Capital group ID", "capital"),),
}


__all__ = [
    "BusinessPromptState",
    "actions",
    "execute",
]
