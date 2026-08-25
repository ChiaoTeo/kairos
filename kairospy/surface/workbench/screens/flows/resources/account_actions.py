"""Selected Account runtime actions for the Resources Workbench slice."""

from __future__ import annotations

from typing import Any, Mapping

from kairospy.investment.apps.account.application.cli import AccountCliApplication

from ....widgets import ActionItem


ACCOUNT_ACTIONS = (
    ActionItem("overview", "账户概览", "查询账户身份和汇总事实", "1"),
    ActionItem("assets", "资产与余额", "查询账户资产余额", "2"),
    ActionItem("positions", "交易仓位", "查询当前持仓", "3"),
    ActionItem("orders", "订单管理", "未完成、历史、成交和订单写操作", "4"),
    ActionItem("earn", "理财与质押", "查询 Earn holdings", "5"),
    ActionItem("fees", "费率与等级", "按产品和交易对查询真实费率", "6"),
    ActionItem("transfer", "资金划转", "在同一交易所账户和分区间转移资产", "7"),
    ActionItem("show", "查看账户配置", "显示脱敏配置", "8"),
    ActionItem("doctor", "运行账户诊断", "检查配置和运行准备", "9"),
    ActionItem("credentials", "查看凭据列表", "只显示凭据元数据", "10"),
    ActionItem("connection", "账户连接设置", "验证、修改或停用当前账户连接", "11"),
)


def execute(
    state: Any,
    record: Mapping[str, Any],
    action: str,
    value: str | None = None,
) -> Any:
    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的 Workspace")
    account_id = str(record.get("account_id") or "")
    if not account_id:
        raise ValueError("所选账户缺少 Account ID")
    application = AccountCliApplication(state.owner)
    query = ("--account-id", account_id, "standalone")
    if action in {"overview", "assets", "positions"}:
        return application.run((*query, action))
    if action == "earn":
        return application.run((*query, "earn-holdings"))
    if action == "fees":
        scope = (value or "spot:BTCUSDT").strip()
        if ":" not in scope:
            raise ValueError("费率范围必须使用 产品:交易对 格式")
        product, symbol = (part.strip() for part in scope.split(":", 1))
        if not product or not symbol:
            raise ValueError("产品和交易对不能为空")
        return application.run(
            (*query, "fees", "--product", product, "--symbol", symbol)
        )
    if action == "transfer":
        capabilities = record.get("capabilities")
        allowed = (
            "transfer" in {str(item) for item in capabilities}
            if isinstance(capabilities, list)
            else str(record.get("credential_role") or "readonly").lower()
            in {"transfer", "admin"}
        )
        allowed = allowed or bool(
            str(record.get("capital_controller_account_id") or "").strip()
        )
        return {
            "account_id": account_id,
            "capability": "transfer",
            "available": allowed,
            "status": "preview-required" if allowed else "not-authorized",
            "message": (
                "资金划转可用；进入后必须先预览，再明确确认执行。"
                if allowed
                else "当前账户凭据不具备资金划转能力。"
            ),
        }
    if action == "show":
        return application.run(("show", "--account-id", account_id))
    if action == "doctor":
        return application.run(("doctor", "--account-id", account_id))
    if action == "credentials":
        return application.run(("credential-list",))
    raise ValueError(f"账户操作需要订单子向导：{action}")


__all__ = ["ACCOUNT_ACTIONS", "execute"]
