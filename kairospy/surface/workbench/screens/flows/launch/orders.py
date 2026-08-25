"""Order workflow through Account and Execution owners for Launch."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from kairospy.investment.apps.account.application.cli import AccountCliApplication
from kairospy.system.apps.components.application import NativeCliApplication

from ....widgets import ActionItem


ORDER_ACTIONS = (
    ActionItem("open-orders", "未完成订单", "直接查询交易所未完成订单", "1"),
    ActionItem("history", "历史订单", "按 provider symbol 查询历史", "2"),
    ActionItem("fills", "成交记录", "按 provider symbol 查询成交", "3"),
    ActionItem("order", "查询订单", "按 Order ID 查询详情", "4"),
    ActionItem("submit", "提交订单", "直接向账户 Provider 提交订单", "5"),
    ActionItem("cancel", "撤销订单", "撤销指定订单", "6"),
    ActionItem("replace", "修改订单", "撤换为新的订单请求", "7"),
)

_SEGMENT_DESCRIPTIONS = {
    "funding": "资金账户",
    "spot": "现货交易",
    "cross_margin": "全仓杠杆",
    "isolated_margin": "逐仓杠杆",
    "usd_m_futures": "U 本位合约",
    "coin_m_futures": "币本位合约",
    "options": "期权",
}


def order_segments(account: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the configured order scopes in their account-owned order."""

    return tuple(str(item) for item in account.get("segments") or ())


def order_segment_actions(account: Mapping[str, Any]) -> tuple[ActionItem, ...]:
    """Present closed segment values as numbered navigation choices."""

    return tuple(
        ActionItem(
            segment,
            segment,
            _SEGMENT_DESCRIPTIONS.get(segment, "交易分区"),
            str(index),
        )
        for index, segment in enumerate(order_segments(account), 1)
    )


@dataclass(slots=True)
class OrderPromptState:
    action: str
    account: dict[str, Any]
    values: dict[str, str] = field(default_factory=dict)

    @property
    def dangerous(self) -> bool:
        return self.action in {"submit", "cancel", "replace"}

    def next_prompt(self) -> tuple[str, str, str] | None:
        for name, label, default in self._steps():
            if name not in self.values:
                detail = (
                    f"直接回车使用 {default}；输入 /back 取消。"
                    if default
                    else "输入 /back 取消。"
                )
                return name, label, detail
        return None

    def accept(self, name: str, raw: str) -> None:
        default = next(
            default for field, _label, default in self._steps() if field == name
        )
        value = raw.strip() or default
        if (
            name in {"order-id", "instrument-id", "quantity", "replacement-order-id"}
            and not value
        ):
            raise ValueError(f"{name} 不能为空")
        if name == "side" and value not in {"buy", "sell"}:
            raise ValueError("side 必须是 buy 或 sell")
        if name == "order-type" and value not in {"market", "limit"}:
            raise ValueError("order type 必须是 market 或 limit")
        self.values[name] = value

    def summary(self) -> dict[str, Any]:
        return {
            "scope": "direct-provider",
            "account_id": self.account_id,
            "provider": self.account.get("integration_provider")
            or self.account.get("broker")
            or "unknown",
            "environment": self.account.get("environment") or "unknown",
            "segment": self.segment
            or ("待选择" if len(self.segments) > 1 else "default"),
            "action": self.action,
            **self.values,
        }

    @property
    def account_id(self) -> str:
        return str(self.account.get("account_id") or "")

    @property
    def segment(self) -> str | None:
        selected = self.values.get("segment")
        if selected:
            return selected
        return self.segments[0] if len(self.segments) == 1 else None

    @property
    def segments(self) -> tuple[str, ...]:
        return order_segments(self.account)

    def _steps(self) -> tuple[tuple[str, str, str], ...]:
        symbol_default = (
            "BTCUSDT"
            if str(
                self.account.get("integration_provider")
                or self.account.get("broker")
                or ""
            ).lower()
            == "binance"
            else ""
        )
        if self.action == "open-orders":
            return ()
        if self.action in {"history", "fills"}:
            return (("symbol", "Provider symbol", symbol_default),)
        if self.action == "order":
            return (
                ("order-id", "Order ID", ""),
                ("symbol", "Provider symbol", symbol_default),
            )
        if self.action == "submit":
            steps = [
                ("order-id", "Order ID", ""),
                ("instrument-id", "Instrument ID", ""),
                ("symbol", "Provider symbol", ""),
                ("quantity", "Quantity", ""),
                ("side", "Side（buy / sell）", "buy"),
                ("order-type", "Order type（market / limit）", "market"),
            ]
            if self.values.get("order-type") == "limit":
                steps.append(("limit-price", "Limit price", ""))
            return tuple(steps)
        if self.action == "cancel":
            return (("order-id", "Order ID", ""),)
        steps = [
            ("order-id", "Target Order ID", ""),
            ("replacement-order-id", "Replacement Order ID", ""),
            ("instrument-id", "Instrument ID", ""),
            ("symbol", "Provider symbol（可留空）", ""),
            ("quantity", "New quantity", ""),
            ("limit-price", "New limit price（可留空）", ""),
        ]
        return tuple(steps)


def execute(state: Any, prompt: OrderPromptState) -> Any:
    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的 Workspace")
    if not prompt.account_id:
        raise ValueError("所选账户缺少 Account ID")
    binding_arguments = [
        "trading-binding",
        "--account-id",
        prompt.account_id,
        "--access",
        "trade" if prompt.dangerous else "read",
    ]
    if prompt.segment:
        binding_arguments.extend(("--segment", prompt.segment))
    binding = AccountCliApplication(state.owner).run(binding_arguments)
    arguments = ["standalone", "--binding-json", json.dumps(binding), prompt.action]
    values = prompt.values
    if prompt.action in {"history", "fills"} and values.get("symbol"):
        arguments.extend(("--symbol", values["symbol"]))
    elif prompt.action == "order":
        arguments.extend(("--order-id", values["order-id"]))
        if values.get("symbol"):
            arguments.extend(("--symbol", values["symbol"]))
    elif prompt.action == "submit":
        arguments.extend(
            (
                "--account-id",
                prompt.account_id,
                "--order-id",
                values["order-id"],
                "--instrument-id",
                values["instrument-id"],
                "--symbol",
                values["symbol"] or values["instrument-id"],
                "--quantity",
                values["quantity"],
                "--side",
                values["side"],
                "--order-type",
                values["order-type"],
            )
        )
        if values.get("limit-price"):
            arguments.extend(("--limit-price", values["limit-price"]))
    elif prompt.action == "cancel":
        arguments.extend(
            ("--account-id", prompt.account_id, "--order-id", values["order-id"])
        )
    elif prompt.action == "replace":
        arguments.extend(
            (
                "--account-id",
                prompt.account_id,
                "--target-order-id",
                values["order-id"],
                "--order-id",
                values["replacement-order-id"],
                "--instrument-id",
                values["instrument-id"],
                "--quantity",
                values["quantity"],
            )
        )
        if values.get("symbol"):
            arguments.extend(("--symbol", values["symbol"]))
        if values.get("limit-price"):
            arguments.extend(("--limit-price", values["limit-price"]))
    return NativeCliApplication(state.owner).run("execution", arguments)


def preview(prompt: OrderPromptState) -> dict[str, Any]:
    return {"status": "preview", **prompt.summary()}


__all__ = [
    "ORDER_ACTIONS",
    "OrderPromptState",
    "execute",
    "order_segment_actions",
    "order_segments",
    "preview",
]
