"""Selected Account runtime actions for the Account Workbench slice."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Mapping

from kairospy.investment.apps.account.application.cli import AccountCliApplication

from ....widgets import ActionItem


class AccountAction(StrEnum):
    """Stable actions owned by one selected trading account."""

    OVERVIEW = "overview"
    ASSETS = "assets"
    POSITIONS = "positions"
    ORDERS = "orders"
    FUNDS = "funds"
    DOCTOR = "doctor"
    CONNECTION = "connection"


class AccountFundsAction(StrEnum):
    """Stable actions in the selected account's funds subtask."""

    EARN = "earn"
    FEES = "fees"
    TRANSFER = "transfer"


ACCOUNT_ACTIONS = (
    ActionItem(
        AccountAction.OVERVIEW, "查看账户概览", "读取账户身份、权限和整体状态", "1"
    ),
    ActionItem(
        AccountAction.ASSETS, "查看资产与余额", "读取账户中的资产和可用余额", "2"
    ),
    ActionItem(AccountAction.POSITIONS, "查看当前持仓", "读取当前交易仓位和方向", "3"),
    ActionItem(
        AccountAction.ORDERS,
        "管理订单与成交",
        "查看订单、成交或发起明确的订单操作",
        "4",
    ),
    ActionItem(
        AccountAction.FUNDS,
        "资金、理财与费率",
        "查看理财和费率，或在账户分区间划转",
        "5",
    ),
    ActionItem(
        AccountAction.DOCTOR, "检查账户问题", "检查连接、权限和账户运行条件", "6"
    ),
    ActionItem(
        AccountAction.CONNECTION,
        "管理账户连接",
        "前往同一账户的连接、凭据与访问设置",
        "7",
    ),
)

ACCOUNT_FUNDS_ACTIONS = (
    ActionItem(
        AccountFundsAction.EARN, "查看理财与质押", "读取当前理财产品持有情况", "1"
    ),
    ActionItem(
        AccountFundsAction.FEES, "查看费率与等级", "按产品和交易对读取实际费率", "2"
    ),
    ActionItem(
        AccountFundsAction.TRANSFER,
        "发起账户内划转",
        "在同一交易所账户和分区间转移资产",
        "3",
    ),
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
    try:
        selected: AccountAction | AccountFundsAction = AccountAction(action)
    except ValueError:
        selected = AccountFundsAction(action)
    if selected in {
        AccountAction.OVERVIEW,
        AccountAction.ASSETS,
        AccountAction.POSITIONS,
    }:
        return application.run((*query, selected.value))
    if selected is AccountFundsAction.EARN:
        return application.run((*query, "earn-holdings"))
    if selected is AccountFundsAction.FEES:
        scope = (value or "spot:BTCUSDT").strip()
        if ":" not in scope:
            raise ValueError("费率范围必须使用 产品:交易对 格式")
        product, symbol = (part.strip() for part in scope.split(":", 1))
        if not product or not symbol:
            raise ValueError("产品和交易对不能为空")
        return application.run(
            (*query, "fees", "--product", product, "--symbol", symbol)
        )
    if selected is AccountFundsAction.TRANSFER:
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
    if selected is AccountAction.DOCTOR:
        return application.run(("doctor", "--account-id", account_id))
    raise ValueError(f"账户操作需要进入对应子任务：{selected.value}")


__all__ = [
    "ACCOUNT_ACTIONS",
    "ACCOUNT_FUNDS_ACTIONS",
    "AccountAction",
    "AccountFundsAction",
    "execute",
]
