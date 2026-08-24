"""Launch-instance-scoped Execution workflow for the command screen."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from kairospy.system.apps.components.application import NativeCliApplication

from ...widgets import ActionItem


EXECUTION_ACTIONS = tuple(
    ActionItem(action, label, description, str(index))
    for index, (action, label, description) in enumerate(
        (
            ("status", "服务状态", "读取 Execution 健康状态"),
            ("snapshot", "运行时快照", "读取当前执行状态"),
            ("routes", "执行路由", "查看 route 和账户绑定"),
            ("orders", "全部订单", "查看运行实例订单"),
            ("open-orders", "未完成订单", "查看活动订单"),
            ("history", "历史订单", "查看历史订单"),
            ("fills", "成交记录", "查看成交"),
            ("events", "生命周期事件", "查看订单事件"),
            ("audit", "审计记录", "查看审计事实"),
            ("inspect", "检查订单", "按 Order ID 查看"),
            ("trace", "追踪订单", "追踪订单生命周期"),
            ("journal", "订单 Journal", "查看订单 journal"),
            ("reconcile", "请求对账", "触发 Execution 对账"),
            ("submit", "提交订单", "向当前 Execution Server 提交"),
            ("cancel", "撤销订单", "撤销当前实例订单"),
            ("replace", "修改订单", "修改数量或限价"),
        ),
        1,
    )
)


@dataclass(slots=True)
class ExecutionPromptState:
    action: str
    launch: Mapping[str, Any]
    values: dict[str, str] = field(default_factory=dict)

    @property
    def dangerous(self) -> bool:
        return self.action in {"reconcile", "submit", "cancel", "replace"}

    def next_prompt(self) -> tuple[str, str, str] | None:
        for name, label, default in self._steps():
            if name not in self.values:
                return (
                    name,
                    label,
                    f"直接回车使用 {default}；输入 /back 取消。"
                    if default
                    else "输入 /back 取消。",
                )
        return None

    def accept(self, name: str, raw: str) -> None:
        default = next(
            default for field, _label, default in self._steps() if field == name
        )
        value = raw.strip() or default
        if name in {
            "order-id",
            "account-id",
            "instrument-id",
            "quantity",
            "route-id",
        } and not value:
            raise ValueError(f"{name} 不能为空")
        if name == "side" and value not in {"buy", "sell"}:
            raise ValueError("side 必须是 buy 或 sell")
        if name == "order-type" and value not in {"market", "limit"}:
            raise ValueError("order type 必须是 market 或 limit")
        self.values[name] = value

    def summary(self) -> dict[str, Any]:
        return {
            "scope": "launch-instance",
            "launch_id": self.launch_id,
            "instance_id": self.instance_id,
            "mode": self.mode,
            "action": self.action,
            **self.values,
        }

    @property
    def launch_id(self) -> str:
        return str(self.launch.get("launch_id") or "")

    @property
    def instance_id(self) -> str:
        return str(self.launch.get("instance_id") or "")

    @property
    def mode(self) -> str:
        return str(self.launch.get("mode") or "")

    def _steps(self) -> tuple[tuple[str, str, str], ...]:
        if self.action in {"inspect", "trace", "journal"}:
            return (("order-id", "Order ID", ""),)
        if self.action == "cancel":
            return (
                ("order-id", "Order ID", ""),
                ("reason", "撤单原因", "manual cancel"),
            )
        if self.action == "replace":
            return (
                ("order-id", "Order ID", ""),
                ("quantity", "New quantity", ""),
                ("limit-price", "New limit price（可留空）", ""),
            )
        if self.action == "submit":
            steps = [
                ("order-id", "Order ID", ""),
                ("account-id", "Account ID", ""),
                ("segment-key", "Segment key", "spot"),
                ("instrument-id", "Instrument ID", ""),
                ("quantity", "Quantity", ""),
                ("route-id", "Execution route ID", ""),
                ("side", "Side（buy / sell）", "buy"),
                ("order-type", "Order type（market / limit）", "market"),
            ]
            if self.values.get("order-type") == "limit":
                steps.append(("limit-price", "Limit price", ""))
            return tuple(steps)
        return ()


def execute(state: Any, prompt: ExecutionPromptState) -> Any:
    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的 Workspace")
    if not prompt.launch_id or not prompt.instance_id or not prompt.mode:
        raise ValueError("必须先选择具体 Launch instance")
    arguments = [
        "connected",
        "--mode",
        prompt.mode,
        "--launch-id",
        prompt.launch_id,
        "--instance-id",
        prompt.instance_id,
        prompt.action,
    ]
    values = prompt.values
    if prompt.action in {"inspect", "trace", "journal", "cancel", "replace"}:
        arguments.extend(("--order-id", values["order-id"]))
    if prompt.action == "cancel":
        arguments.extend(("--reason", values["reason"]))
    elif prompt.action == "replace":
        arguments.extend(("--quantity", values["quantity"]))
        if values.get("limit-price"):
            arguments.extend(("--limit-price", values["limit-price"]))
    elif prompt.action == "submit":
        arguments.extend(
            (
                "--order-id",
                values["order-id"],
                "--account-id",
                values["account-id"],
                "--segment-key",
                values["segment-key"],
                "--instrument-id",
                values["instrument-id"],
                "--quantity",
                values["quantity"],
                "--execution-route-id",
                values["route-id"],
                "--side",
                values["side"],
                "--order-type",
                values["order-type"],
            )
        )
        if values.get("limit-price"):
            arguments.extend(("--limit-price", values["limit-price"]))
    return NativeCliApplication(state.owner).run("execution", arguments)


def preview(prompt: ExecutionPromptState) -> dict[str, Any]:
    return {"status": "preview", **prompt.summary()}


__all__ = [
    "EXECUTION_ACTIONS",
    "ExecutionPromptState",
    "execute",
    "preview",
]
