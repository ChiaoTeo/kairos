"""Stable root catalog and product labels for Workbench navigation."""

from __future__ import annotations

from ...widgets import ActionItem


HOME_ACTIONS = (
    ActionItem("market", "市场与标的", "搜索标的并查看实时或历史行情", "1"),
    ActionItem("strategy", "策略与运行", "管理运行方案、启动实例并查看运行历史", "2"),
    ActionItem("account", "账户与交易", "查看账户、余额、持仓、订单和资金操作", "3"),
    ActionItem("resources", "连接与配置", "管理账户、行情、模型和通知连接", "4"),
    ActionItem("research", "数据与回测", "准备研究数据并运行策略回测", "5"),
    ActionItem("operations", "运行中心", "查看当前运行拓扑、依赖和异常", "6"),
    ActionItem("project", "项目管理", "查看、检查、创建、打开或切换项目", "7"),
)

MARKET_ACTIONS = (
    ActionItem("search", "查找可以交易的标的", "输入 AAPL、比特币或 BTCUSDT", "1"),
    ActionItem("live", "我的实时行情", "查看、添加或退出当前会话关注的行情", "c"),
    ActionItem("download", "下载历史行情", "选择时间范围并保存行情数据", "2"),
    ActionItem("datasets", "查看已下载的历史行情", "浏览项目中的本地行情数据", "3"),
    ActionItem("catalog", "浏览交易所与交易品种", "按交易所、品种或具体市场浏览", "4"),
)

# These remain available to experienced operators, but do not compete with the
# three everyday tasks in the visible Market menu.
MARKET_ADVANCED_ACTIONS = (
    ActionItem("replay", "回放本地行情", "将 JSONL 行情事件送入独立回放", "r"),
    ActionItem("diagnostics", "诊断问题", "检查市场定义和标的目录映射", "d"),
    ActionItem("advanced", "高级市场标识", "手动输入完整的内部市场标识", "a"),
)

RESUME_MARKET_SEARCH_ACTION = ActionItem(
    "resume-search",
    "继续上次标的搜索",
    "目录已经准备好，返回开始接入前的搜索",
    "u",
)

MISSING_MARKET_ACTIONS = (
    ActionItem("prepare", "准备这个标的目录", "选择交易所和品种，检查可用来源", "1"),
    ActionItem("retry", "重新搜索", "再次搜索刚才输入的代码或名称", "2"),
    ActionItem("catalog", "浏览现有标的目录", "从已经可用的目录中查找", "3"),
)

CATALOG_EXCHANGE_ACTIONS = (
    ActionItem("exchange:nasdaq", "纳斯达克", "美国股票交易所", "1"),
    ActionItem("exchange:nyse", "纽约证券交易所", "美国股票交易所", "2"),
    ActionItem("exchange:amex", "美国证券交易所", "美国股票交易所", "3"),
    ActionItem("exchange:binance", "币安", "数字资产现货与衍生品", "4"),
    ActionItem("exchange:okx", "OKX", "数字资产现货与衍生品", "5"),
    ActionItem("exchange:hyperliquid", "Hyperliquid", "现货与永续合约", "6"),
)

CATALOG_INSTRUMENT_ACTIONS = (
    ActionItem("equity", "股票", "交易所挂牌股票", "1"),
    ActionItem("spot", "现货", "现货交易对", "2"),
    ActionItem("perpetual", "永续合约", "没有到期日的衍生品", "3"),
    ActionItem("future", "交割合约", "具有到期日的合约", "4"),
    ActionItem("option", "期权", "看涨与看跌期权", "5"),
)

REFERENCE_ACTIONS = (
    ActionItem("status", "查看目录准备进度", "查看各来源正在准备什么以及失败原因", "s"),
    ActionItem("exchanges", "查找交易所", "例如纳斯达克、纽约证券交易所或币安", "1"),
    ActionItem("instruments", "查找交易品种", "股票、现货、期货、期权与指数", "2"),
    ActionItem("markets", "查找具体市场", "查看一个品种可以在哪些交易所交易", "3"),
    ActionItem("option-chain", "查看期权链", "按股票或其他标的查看有效期权", "4"),
    ActionItem("assets", "高级：查找资产", "查看货币、证券等目录资产", "5"),
)

STRATEGY_ACTIONS = (
    ActionItem("launch", "运行方案", "配置、校验、启动并查看运行实例", "1"),
)

RESOURCE_ACTIONS = (
    ActionItem("accounts", "交易账户", "账户身份、权限与连接验证", "1"),
    ActionItem("data", "市场数据", "标的目录与行情数据连接", "2"),
    ActionItem("models", "AI 模型", "模型服务端点、凭据与可用模型", "3"),
    ActionItem("notifications", "通知提醒", "飞书、Telegram 等通知目标", "4"),
    ActionItem("check", "检查所有连接", "汇总未配置、待验证与失败原因", "5"),
)

AI_MODEL_ACTIONS = (
    ActionItem("models", "模型列表", "查看、添加和测试可用模型", "1"),
    ActionItem(
        "model_endpoints",
        "供应商账号列表",
        "管理模型供应商、API 地址和访问凭据",
        "2",
    ),
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
    "market": "市场与标的",
    "reference": "市场与标的 / 标的目录",
    "strategy": "策略与运行",
    "account": "账户与交易",
    "resources": "连接与配置",
    "research": "数据与回测",
    "operations": "运行中心",
}


__all__ = [
    "AI_MODEL_ACTIONS",
    "CATALOG_EXCHANGE_ACTIONS",
    "CATALOG_INSTRUMENT_ACTIONS",
    "HOME_ACTIONS",
    "MARKET_ADVANCED_ACTIONS",
    "MISSING_MARKET_ACTIONS",
    "RESUME_MARKET_SEARCH_ACTION",
    "SECTION_ACTIONS",
    "SECTION_LABELS",
]
