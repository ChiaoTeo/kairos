"""Visible shell actions and product labels for the Workbench."""

from __future__ import annotations

from ..widgets import ActionItem


HOME_ACTIONS = (
    ActionItem("market", "查看市场行情", "查看实时报价、历史行情和行情回放", "1"),
    ActionItem("reference", "搜索交易标的", "查找股票、期货、期权和交易所", "2"),
    ActionItem("strategy", "策略管理", "管理运行方案、启动实例并查看运行历史", "3"),
    ActionItem(
        "resources", "运行前检查", "检查账户、行情、模型连接和通知是否就绪", "4"
    ),
    ActionItem("research", "数据与回测", "准备研究数据并运行策略回测", "5"),
    ActionItem("operations", "运行中心", "查看当前运行拓扑、依赖和异常", "6"),
    ActionItem("project", "项目管理", "查看、检查、创建、打开或切换项目", "7"),
)

MARKET_ACTIONS = (
    ActionItem("search", "搜索标的并查看行情", "按代码或名称搜索有效标的", "1"),
    ActionItem("download", "下载历史行情", "选择时间范围并保存行情数据", "2"),
    ActionItem("datasets", "查看本地行情数据", "浏览已准备的数据集", "3"),
)

# These remain available to experienced operators, but do not compete with the
# three everyday tasks in the visible Market menu.
MARKET_ADVANCED_ACTIONS = (
    ActionItem("replay", "回放本地行情", "将 JSONL 行情事件送入独立回放", "r"),
    ActionItem("connected", "连接运行中的行情服务", "查看实时服务和订阅状态", "c"),
    ActionItem("diagnostics", "诊断问题", "检查市场定义和 Reference 映射", "d"),
    ActionItem("advanced", "高级市场标识", "手动输入完整市场标识", "a"),
)

REFERENCE_ACTIONS = (
    ActionItem("assets", "查找资产", "货币、股票及其他可计价资产", "1"),
    ActionItem("exchanges", "查找交易所", "浏览交易场所及其状态", "2"),
    ActionItem("instruments", "查找合约", "股票、现货、期货、期权与指数", "3"),
    ActionItem("markets", "查找交易标的", "按代码、名称或市场标识搜索", "4"),
    ActionItem("option-chain", "查看期权链", "按标的合约查看有效期权", "5"),
)

STRATEGY_ACTIONS = (
    ActionItem("launch", "运行方案", "配置、校验、启动并查看运行实例", "1"),
)

RESOURCE_ACTIONS = (
    ActionItem("accounts", "交易账户", "账户身份、权限与连接验证", "1"),
    ActionItem("data", "市场数据", "Reference 与行情数据连接", "2"),
    ActionItem("models", "模型连接", "模型服务、Endpoint、凭据与可用模型", "3"),
    ActionItem("notifications", "通知提醒", "飞书、Telegram 等通知目标", "4"),
    ActionItem("check", "检查所有连接", "汇总未配置、待验证与失败原因", "5"),
)

RESEARCH_ACTIONS = (
    ActionItem("data", "数据准备", "Dataset、需求计划、执行和 Data Gate", "1"),
    ActionItem("research", "研究流程", "Research Plan 与 Research Gate", "2"),
)

OPERATIONS_ACTIONS = (
    ActionItem("refresh", "刷新运行结构", "重新读取活动实例、共享服务和支撑进程", "r"),
)

SECTION_ACTIONS: dict[str, tuple[ActionItem, ...]] = {
    "market": MARKET_ACTIONS,
    "reference": REFERENCE_ACTIONS,
    "strategy": STRATEGY_ACTIONS,
    "resources": RESOURCE_ACTIONS,
    "research": RESEARCH_ACTIONS,
    "operations": OPERATIONS_ACTIONS,
}

SECTION_LABELS: dict[str, str] = {
    "market": "市场行情",
    "reference": "市场标的",
    "strategy": "策略管理",
    "resources": "运行准备",
    "research": "数据研究",
    "operations": "运行中心",
}


__all__ = [
    "HOME_ACTIONS",
    "MARKET_ADVANCED_ACTIONS",
    "SECTION_ACTIONS",
    "SECTION_LABELS",
]
