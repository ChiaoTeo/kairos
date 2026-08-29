"""Stable root catalog and product labels for Workbench navigation."""

from __future__ import annotations

from enum import StrEnum

from ...widgets import ActionItem
from .identity import Section


class MarketTask(StrEnum):
    SEARCH = "search"
    LIVE = "live"
    DOWNLOAD = "download"
    DATASETS = "datasets"
    CATALOG = "catalog"


class ReferenceTask(StrEnum):
    SOURCES = "sources"
    STATUS = "status"
    EXCHANGES = "exchanges"
    INSTRUMENTS = "instruments"
    MARKETS = "markets"
    OPTION_CHAIN = "option-chain"
    ASSETS = "assets"


class StrategyTask(StrEnum):
    LAUNCH = "launch"


class ResourceTask(StrEnum):
    ACCOUNTS = "accounts"
    DATA = "data"
    MODELS = "models"
    NOTIFICATIONS = "notifications"
    CHECK = "check"


class AiModelTask(StrEnum):
    MODELS = "models"
    ENDPOINTS = "model_endpoints"


class ResearchTask(StrEnum):
    DATA = "data"
    WORKFLOW = "research"


class OperationsTask(StrEnum):
    REFRESH = "refresh"


HOME_ACTIONS = (
    ActionItem(Section.MARKET, "市场与标的", "搜索标的并查看实时或历史行情", "1"),
    ActionItem(
        Section.STRATEGY, "策略与运行", "管理运行方案、启动实例并查看运行历史", "2"
    ),
    ActionItem(
        Section.ACCOUNT, "账户与交易", "查看账户、余额、持仓、订单和资金操作", "3"
    ),
    ActionItem(Section.RESOURCES, "连接与配置", "管理账户、行情、模型和通知连接", "4"),
    ActionItem(Section.RESEARCH, "数据与回测", "准备研究数据并运行策略回测", "5"),
    ActionItem(Section.OPERATIONS, "运行中心", "查看当前运行拓扑、依赖和异常", "6"),
    ActionItem(Section.PROJECT, "项目管理", "查看、检查、创建、打开或切换项目", "7"),
)

MARKET_ACTIONS = (
    ActionItem(
        MarketTask.SEARCH, "查找可以交易的标的", "输入 AAPL、比特币或 BTCUSDT", "1"
    ),
    ActionItem(
        MarketTask.LIVE, "我的实时行情", "查看、添加或退出当前会话关注的行情", "2"
    ),
    ActionItem(MarketTask.DOWNLOAD, "下载历史行情", "选择时间范围并保存行情数据", "3"),
    ActionItem(
        MarketTask.DATASETS, "查看已下载的历史行情", "浏览项目中的本地行情数据", "4"
    ),
    ActionItem(
        MarketTask.CATALOG, "浏览交易所与交易品种", "按交易所、品种或具体市场浏览", "5"
    ),
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
    "1",
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
    ActionItem("exchange:binance", "币安数字资产市场", "现货与衍生品目录", "4"),
    ActionItem("exchange:okx", "OKX", "数字资产现货与衍生品", "5"),
    ActionItem("exchange:hyperliquid", "Hyperliquid", "现货与永续合约", "6"),
    ActionItem(
        "provider:binance-equity",
        "币安股票交易服务",
        "币安提供的美国股票与 ETF 可交易目录；不是上市交易所目录",
        "7",
    ),
)

CATALOG_INSTRUMENT_ACTIONS = (
    ActionItem("equity", "股票", "交易所挂牌股票", "1"),
    ActionItem("spot", "现货", "现货交易对", "2"),
    ActionItem("perpetual", "永续合约", "没有到期日的衍生品", "3"),
    ActionItem("future", "交割合约", "具有到期日的合约", "4"),
    ActionItem("option", "期权", "看涨与看跌期权", "5"),
)

REFERENCE_ACTIONS = (
    ActionItem(
        ReferenceTask.SOURCES, "管理目录来源", "查看、更新、暂停或继续已配置的来源", "1"
    ),
    ActionItem(
        ReferenceTask.STATUS,
        "查看目录准备进度",
        "查看各来源正在准备什么以及失败原因",
        "2",
    ),
    ActionItem(
        ReferenceTask.EXCHANGES, "查找交易所", "例如纳斯达克、纽约证券交易所或币安", "3"
    ),
    ActionItem(
        ReferenceTask.INSTRUMENTS, "查找交易品种", "股票、现货、期货、期权与指数", "4"
    ),
    ActionItem(
        ReferenceTask.MARKETS,
        "查看品种在哪里可以交易",
        "区分交易所市场与服务商渠道",
        "5",
    ),
    ActionItem(
        ReferenceTask.OPTION_CHAIN, "查看期权链", "按股票或其他标的查看有效期权", "6"
    ),
    ActionItem(ReferenceTask.ASSETS, "高级：查找资产", "查看货币、证券等目录资产", "7"),
)

STRATEGY_ACTIONS = (
    ActionItem(StrategyTask.LAUNCH, "运行方案", "配置、校验、启动并查看运行实例", "1"),
)

RESOURCE_ACTIONS = (
    ActionItem(ResourceTask.ACCOUNTS, "交易账户", "账户身份、权限与连接验证", "1"),
    ActionItem(ResourceTask.DATA, "市场数据", "标的目录与行情数据连接", "2"),
    ActionItem(ResourceTask.MODELS, "AI 模型", "模型服务端点、凭据与可用模型", "3"),
    ActionItem(
        ResourceTask.NOTIFICATIONS, "通知提醒", "飞书、Telegram 等通知目标", "4"
    ),
    ActionItem(ResourceTask.CHECK, "检查所有连接", "汇总未配置、待验证与失败原因", "5"),
)

AI_MODEL_ACTIONS = (
    ActionItem(AiModelTask.MODELS, "模型列表", "查看、添加和测试可用模型", "1"),
    ActionItem(
        AiModelTask.ENDPOINTS,
        "供应商账号列表",
        "管理模型供应商、API 地址和访问凭据",
        "2",
    ),
)

RESEARCH_ACTIONS = (
    ActionItem(
        ResearchTask.DATA, "数据准备", "Dataset、需求计划、执行和 Data Gate", "1"
    ),
    ActionItem(
        ResearchTask.WORKFLOW, "研究流程", "Research Plan 与 Research Gate", "2"
    ),
)

OPERATIONS_ACTIONS = (
    ActionItem(
        OperationsTask.REFRESH,
        "刷新运行结构",
        "重新读取活动实例、共享服务和支撑进程",
        "1",
    ),
)

SECTION_ACTIONS: dict[Section, tuple[ActionItem, ...]] = {
    Section.MARKET: MARKET_ACTIONS,
    Section.REFERENCE: REFERENCE_ACTIONS,
    Section.STRATEGY: STRATEGY_ACTIONS,
    Section.RESOURCES: RESOURCE_ACTIONS,
    Section.RESEARCH: RESEARCH_ACTIONS,
    Section.OPERATIONS: OPERATIONS_ACTIONS,
}

SECTION_LABELS: dict[Section, str] = {
    Section.MARKET: "市场与标的",
    Section.REFERENCE: "市场与标的 / 标的目录",
    Section.STRATEGY: "策略与运行",
    Section.ACCOUNT: "账户与交易",
    Section.RESOURCES: "连接与配置",
    Section.RESEARCH: "数据与回测",
    Section.OPERATIONS: "运行中心",
}


__all__ = [
    "AI_MODEL_ACTIONS",
    "AiModelTask",
    "CATALOG_EXCHANGE_ACTIONS",
    "CATALOG_INSTRUMENT_ACTIONS",
    "HOME_ACTIONS",
    "MARKET_ADVANCED_ACTIONS",
    "MarketTask",
    "MISSING_MARKET_ACTIONS",
    "RESUME_MARKET_SEARCH_ACTION",
    "OperationsTask",
    "ReferenceTask",
    "ResearchTask",
    "ResourceTask",
    "SECTION_ACTIONS",
    "SECTION_LABELS",
    "StrategyTask",
]
