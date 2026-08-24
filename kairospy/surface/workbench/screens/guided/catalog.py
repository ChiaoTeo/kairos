"""Visible actions and labels for the single-input Workbench."""

from __future__ import annotations

from ...widgets import ActionItem


HOME_ACTIONS = (
    ActionItem("market", "查看市场行情", "报价 · 历史行情 · 行情回放", "1"),
    ActionItem("reference", "查找市场标的", "股票 · 期货 · 期权 · 交易市场", "2"),
    ActionItem("strategy", "配置并运行策略", "选择策略 · 配置参数 · 启动", "3"),
    ActionItem(
        "resources", "完成运行准备", "账户 · 市场数据 · AI 模型 · 通知提醒", "4"
    ),
    ActionItem("research", "准备数据研究", "研究数据 · 回测数据", "5"),
    ActionItem("operations", "维护系统", "工作区 · 后台进程 · 问题排查", "6"),
)

MARKET_ACTIONS = (
    ActionItem("search", "搜索标的并查看行情", "按代码或名称搜索有效标的", "1"),
    ActionItem("download", "下载历史行情", "选择时间范围并保存行情数据", "2"),
    ActionItem("datasets", "查看本地行情数据", "浏览已准备的数据集", "3"),
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
    ActionItem("launch", "运行列表与控制", "配置、启动、停止和诊断 Launch", "1"),
    ActionItem("observe", "打开运行观测", "查看组件、Launch 与行情状态", "2"),
    ActionItem("once", "刷新一次运行快照", "读取当前 Workspace 状态", "3"),
    ActionItem("doctor", "推荐诊断动作", "根据当前状态给出下一步", "4"),
)

RESOURCE_ACTIONS = (
    ActionItem("accounts", "交易账户", "账户身份、权限与连接验证", "1"),
    ActionItem("data", "市场数据", "Reference 与行情数据连接", "2"),
    ActionItem("models", "AI 模型", "模型服务、凭据与可用模型", "3"),
    ActionItem("notifications", "通知提醒", "飞书、Telegram 等通知目标", "4"),
    ActionItem("check", "检查所有连接", "汇总未配置、待验证与失败原因", "5"),
)

RESEARCH_ACTIONS = (
    ActionItem("data", "数据准备", "Dataset、需求计划、执行和 Data Gate", "1"),
    ActionItem("research", "研究流程", "Research Plan 与 Research Gate", "2"),
)

OPERATIONS_ACTIONS = (
    ActionItem("project", "项目工作区", "创建、检查并安装项目模板", "1"),
    ActionItem("observe", "实时观测", "组件、Launch 与市场状态总览", "2"),
    ActionItem("services", "系统服务", "管理 Reference 与 Market 进程", "3"),
    ActionItem("doctor", "诊断系统", "检查 socket、健康文件与进程锁", "4"),
    ActionItem("repair", "修复 stale 资源", "仅清理可证明已失效的运行资源", "5"),
    ActionItem("config", "高级配置", "路径、配置、Profile 与模型连接", "6"),
    ActionItem("migration", "配置升级", "查看旧格式配置及安全迁移要求", "7"),
    ActionItem("workspace", "Workspace 信息", "查看当前工作区路径和身份", "8"),
    ActionItem("business", "业务工具", "Risk、Capital 与 Provider 集成", "9"),
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
    "strategy": "策略运行",
    "resources": "运行准备",
    "research": "数据研究",
    "operations": "系统维护",
}


__all__ = ["HOME_ACTIONS", "SECTION_ACTIONS", "SECTION_LABELS"]
