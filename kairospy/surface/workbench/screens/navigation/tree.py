"""Workbench 用户任务之间的进入、返回与可用操作。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rich.console import RenderableType
from rich.panel import Panel
from rich.table import Table

from ...widgets import ActionItem, ChoiceInteraction
from ..flows.resources.account_actions import (
    ACCOUNT_ACTIONS as RESOURCE_ACCOUNT_ACTIONS,
)
from ..flows.resources.account_transfers import TRANSFER_RESULT_ACTIONS
from .catalog import (
    AI_MODEL_ACTIONS,
    CATALOG_EXCHANGE_ACTIONS,
    CATALOG_INSTRUMENT_ACTIONS,
    HOME_ACTIONS,
    MISSING_MARKET_ACTIONS,
    RESUME_MARKET_SEARCH_ACTION,
    SECTION_ACTIONS,
    SECTION_LABELS,
)
from ..flows.launch.execution_actions import (
    EXECUTION_ACTIONS as STRATEGY_EXECUTION_ACTIONS,
)
from ..flows.launch.market_actions import (
    MARKET_COMPONENT_ACTIONS as STRATEGY_MARKET_ACTIONS,
)
from ..session import GuidedSession
from ..flows.operations.actions import (
    project_actions,
)
from ..flows.operations.views import (
    LOG_FOLLOW_ACTIONS,
    SUPPORT_ACTIONS,
    service_actions,
    service_display_name,
)
from ..flows.launch.orders import (
    ORDER_ACTIONS as ACCOUNT_ORDER_ACTIONS,
    order_segment_actions,
    order_segments,
)
from ..flows.research.actions import (
    DATA_ACTIONS as RESEARCH_DATA_ACTIONS,
    RESEARCH_ACTIONS as RESEARCH_WORKFLOW_ACTIONS,
)
from ..flows.resources.actions import detail_actions as resource_detail_actions
from ..flows.resources.views import RESOURCE_LABELS, identity, record_summary
from ..flows.reference.actions import CatalogSetupPlanView
from ..flows.launch.actions import (
    ATTACH_ACTIONS as STRATEGY_ATTACH_ACTIONS,
    INSTANCE_ACTIONS as STRATEGY_INSTANCE_ACTIONS,
    LAUNCH_ACTIONS as STRATEGY_LAUNCH_ACTIONS,
    TIMELINE_ACTIONS as STRATEGY_TIMELINE_ACTIONS,
)
from ..flows.market.workspace import (
    LIVE_MARKET_ACTIONS,
    LIVE_MARKET_UNAVAILABLE_ACTIONS,
    live_market_available,
)
from ..flows.market.actions import (
    MARKET_CONTROL_ACTIONS,
    provider_actions as market_provider_actions,
    selected_market_actions,
)
from ..selection import SelectionRecord, selection_records


def _visible(records: tuple[Any, ...]) -> tuple[SelectionRecord, ...]:
    return selection_records(
        records,
        label=record_label,
        description=record_description,
    )


def _parent_context(
    session: GuidedSession, context: tuple[str, ...]
) -> tuple[str, ...] | None:
    """Return the semantic parent without copying or mutating session state."""

    if not context:
        return None
    if context in {("project",), ("operations", "overview")}:
        return ()
    if context in {
        ("operations", "services"),
        ("operations", "instances"),
        ("operations", "supports"),
    }:
        return ("operations", "overview")
    if context == ("market", "providers"):
        return ("market", "selected")
    if context == ("account", "accounts"):
        return ()
    if context == ("account", "selected"):
        return ("account", "accounts")
    if context == ("account", "orders"):
        record = session.account.selected or {}
        return (
            ("account", "order-segments")
            if len(order_segments(record)) > 1
            else ("account", "selected")
        )
    if context == ("account", "order-segments"):
        return ("account", "selected")
    if context == ("account", "transfer-result"):
        return ("account", "selected")
    if context in {
        ("market", "workspace-market-results"),
        ("market", "workspace-subscriptions"),
        ("market", "workspace-subscription-content"),
        ("market", "workspace-snapshot-kind"),
        ("market", "workspace-timeframe"),
        ("market", "workspace-providers"),
    }:
        return ("market", "live")
    if context == ("market", "live-unavailable"):
        return ("market",)
    if context == ("market", "live"):
        return ("market",)
    if context == ("market", "selected"):
        return ("market", "results") if session.market.records else ("market",)
    if context == ("reference",):
        return ("market",)
    if context == ("reference", "selected"):
        kind = session.reference.kind
        return (
            ("reference", kind)
            if kind is not None and session.visible_records
            else ("reference",)
        )
    if context[:2] == ("operations", "service-logs"):
        component = session.operations.selected_service
        return (
            ("operations", "service", component)
            if component is not None
            else ("operations", "services")
        )
    if context[:2] == ("operations", "service"):
        return ("operations", "services")
    if context[:2] == ("operations", "support"):
        return ("operations", "supports")
    if len(context) > 1 and context[0] == "operations":
        return ("operations",)
    if context == ("resources", "model-chat"):
        return ("resources", "selected")
    if context == ("resources", "selected"):
        kind = session.resources.kind
        return (
            ("resources", kind)
            if kind is not None and session.visible_records
            else ("resources",)
        )
    if context in {
        ("resources", "models"),
        ("resources", "model_endpoints"),
    }:
        return ("resources", "ai-models")
    if len(context) > 1 and context[0] == "resources":
        return ("resources",)
    if len(context) > 1 and context[0] == "research":
        return ("research",)
    if context == ("strategy", "selected"):
        return (
            ("strategy", "launches")
            if session.strategy.launch_records
            else ("strategy",)
        )
    if context in {("strategy", "attach"), ("strategy", "instances")}:
        return ("strategy", "selected")
    if context in {("strategy", "components"), ("strategy", "timeline")}:
        return ("strategy", "instance")
    if context in {("strategy", "execution"), ("strategy", "market")}:
        return ("strategy", "components")
    if context == ("strategy", "instance"):
        return (
            ("operations", "instances")
            if session.strategy.instance_entered_from_operations
            else ("strategy", "instances")
        )
    if len(context) > 1 and context[0] == "strategy":
        return ("strategy",)
    if len(context) > 1 and context[0] == "reference":
        if (
            context[1] == "instruments"
            and session.reference.instrument_type is not None
        ):
            return ("reference", "instrument-types")
        return ("reference",)
    if len(context) > 1:
        return (context[0],)
    return ()


def go_back(session: GuidedSession) -> bool:
    """Return by visit history, with semantic parents for unmigrated flows."""

    source = session.context
    stack_target = session.stack_parent()
    target = (
        stack_target if stack_target is not None else _parent_context(session, source)
    )
    if target is None:
        return False
    session.navigation_generation += 1
    if stack_target is not None:
        session.pop_frame()
    if target == ():
        session.home()
        return True

    if source in {
        ("operations", "services"),
        ("operations", "instances"),
        ("operations", "supports"),
    }:
        session.visible_records = session.operations.group_records
    elif source == ("market", "providers"):
        session.visible_records = _visible(session.market.records)
    elif source in {
        ("market", "workspace-market-results"),
        ("market", "workspace-subscriptions"),
        ("market", "workspace-subscription-content"),
        ("market", "workspace-snapshot-kind"),
        ("market", "workspace-timeframe"),
        ("market", "workspace-providers"),
    }:
        session.market.workspace_prompt = None
        session.visible_records = ()
    elif source == ("market", "live"):
        pass
    elif source == ("market", "selected"):
        session.visible_records = _visible(session.market.records)
    elif source == ("account", "selected"):
        session.account.selected = None
        session.visible_records = selection_records(
            session.account.records,
            key=lambda record: identity("accounts", record),
            label=lambda record: identity("accounts", record),
            description=lambda record: record_summary("accounts", record),
        )
    elif source == ("account", "orders"):
        session.account.order_prompt = None
        if target == ("account", "selected"):
            session.account.selected_segment = None
    elif source == ("account", "order-segments"):
        session.account.selected_segment = None
        session.account.order_prompt = None
    elif source == ("account", "transfer-result"):
        session.account.transfer_prompt = None
    elif source == ("reference", "selected"):
        pass
    elif source[:2] == ("operations", "service-logs"):
        pass
    elif source[:2] == ("operations", "service") and source[:2] != (
        "operations",
        "service-logs",
    ):
        session.visible_records = tuple(
            record
            for record in session.operations.inventory_records
            if isinstance(record.value, Mapping)
            and record.value.get("kind") == "service"
        )
    elif source[:2] == ("operations", "support"):
        session.visible_records = tuple(
            record
            for record in session.operations.inventory_records
            if isinstance(record.value, Mapping)
            and record.value.get("kind") == "support"
        )
    elif len(source) > 1 and source[0] == "operations":
        pass
    elif source == ("resources", "model-chat"):
        session.resources.action = None
    elif source == ("resources", "selected"):
        pass
    elif source in {
        ("resources", "models"),
        ("resources", "model_endpoints"),
    }:
        session.resources.kind = None
        session.visible_records = ()
    elif len(source) > 1 and source[0] == "resources":
        pass
    elif len(source) > 1 and source[0] == "research":
        pass
    elif source == ("strategy", "selected"):
        session.visible_records = _visible(session.strategy.launch_records)
    elif source in {("strategy", "attach"), ("strategy", "instances")}:
        if source == ("strategy", "attach"):
            session.strategy.attach_paused = True
        session.visible_records = _visible(session.strategy.launch_records)
    elif source in {
        ("strategy", "instance"),
        ("strategy", "components"),
        ("strategy", "timeline"),
        ("strategy", "execution"),
        ("strategy", "market"),
    }:
        if source in {
            ("strategy", "components"),
            ("strategy", "timeline"),
        }:
            session.visible_records = _visible(session.strategy.instance_records)
        elif source in {
            ("strategy", "execution"),
            ("strategy", "market"),
        }:
            session.visible_records = _visible(session.strategy.component_records)
        elif session.strategy.instance_entered_from_operations:
            session.visible_records = tuple(
                record
                for record in session.operations.inventory_records
                if isinstance(record.value, Mapping)
                and record.value.get("kind") == "run-instance"
            )
            session.strategy.instance_entered_from_operations = False
        else:
            session.visible_records = _visible(session.strategy.instance_records)
    elif len(source) > 1 and source[0] == "strategy":
        pass
    elif len(source) > 1 and source[0] == "reference":
        if source[1] == "instruments" and session.reference.instrument_type is not None:
            session.visible_records = ()
    elif len(source) > 1:
        pass

    session.replace_context(target)
    return True


def back_targets(session: GuidedSession) -> tuple[tuple[str, ...], ...]:
    """Return visited pages first, then semantic parents for unmigrated flows."""

    context = session.context
    targets: list[tuple[str, ...]] = []
    frames = tuple(frame.context for frame in session.navigation_stack)
    frame_index = len(frames) - 1 if frames and frames[-1] == context else None
    while True:
        if frame_index is not None and frame_index > 0:
            frame_index -= 1
            context = frames[frame_index]
        else:
            context = _parent_context(session, context)
        if context is None:
            break
        if frame_index is None and frames and context == frames[-1]:
            frame_index = len(frames) - 1
        if targets and targets[-1] == context:
            continue
        targets.append(context)
        if context == ():
            break
    return tuple(targets)


def back_target_items(session: GuidedSession) -> tuple[ActionItem, ...]:
    """Build numbered return destinations for the shared interaction region."""

    return tuple(
        ActionItem(
            f"navigate-back:{steps}",
            context_label(target, session.root_label),
            "返回上一级" if steps == 1 else "直接返回到此层级",
            str(steps),
        )
        for steps, target in enumerate(back_targets(session), 1)
    )


def action_id(items: tuple[ActionItem, ...], value: str) -> str | None:
    lowered = value.lower()
    for item in items:
        if lowered in {item.id.lower(), (item.shortcut or "").lower()}:
            return item.id
    return None


def _catalog_setup_actions(plan: CatalogSetupPlanView | None) -> tuple[ActionItem, ...]:
    if plan is None:
        return (
            ActionItem("check", "检查准备条件", "查找适合的数据来源和账号要求", "1"),
            ActionItem("change", "重新选择交易所和品种", "修改要准备的目录范围", "2"),
        )
    blockers = set(plan.blockers)
    availability = plan.availability
    actions: list[ActionItem] = []
    if "missing_connection_binding" in blockers:
        actions.append(
            ActionItem(
                "configure-connection",
                "配置所需的数据服务账号",
                "完成后返回这里继续准备",
                "1",
            )
        )
    elif "unsupported_goal" not in blockers:
        actions.append(
            ActionItem(
                "start",
                "开始准备" if availability == "not_configured" else "继续准备",
                "读取来源目录并更新项目中的标的",
                "1",
            )
        )
    actions.append(ActionItem("check", "查看最新进度", "重新读取准备状态和进度", "2"))
    if availability in {"usable", "partially_usable", "stale"}:
        actions.append(
            ActionItem("search-again", "回到原搜索", "再次搜索刚才输入的标的", "3")
        )
    actions.append(
        ActionItem("change", "重新选择交易所和品种", "修改要准备的目录范围", "4")
    )
    return tuple(actions)


def context_items(session: GuidedSession, state: Any) -> tuple[ActionItem, ...]:
    if not session.context:
        return HOME_ACTIONS
    if session.context == ("project",):
        return project_actions(has_project=state.owner is not None)
    if session.context == ("market", "selected"):
        market = session.market.selected
        if market is None:
            return ()
        actions = selected_market_actions(market)
        if session.market.snapshot is not None:
            actions = (*actions, *MARKET_CONTROL_ACTIONS)
        return actions
    if session.context == ("market", "providers"):
        return market_provider_actions(session.market.routes)
    if session.context == ("market", "missing"):
        return MISSING_MARKET_ACTIONS
    if session.context == ("market", "catalog-exchange"):
        return CATALOG_EXCHANGE_ACTIONS
    if session.context == ("market", "catalog-instrument"):
        goal = session.market.catalog_setup_goal
        exchange = goal.exchange_id or "" if goal is not None else ""
        if exchange in {"exchange:nasdaq", "exchange:nyse", "exchange:amex"}:
            return (CATALOG_INSTRUMENT_ACTIONS[0],)
        if exchange == "exchange:hyperliquid":
            return (
                CATALOG_INSTRUMENT_ACTIONS[1],
                CATALOG_INSTRUMENT_ACTIONS[2],
            )
        return CATALOG_INSTRUMENT_ACTIONS[1:]
    if session.context == ("market", "catalog-setup"):
        return _catalog_setup_actions(session.market.catalog_setup_plan)
    if session.context == ("market", "live"):
        return (
            LIVE_MARKET_ACTIONS
            if live_market_available(state)
            else LIVE_MARKET_UNAVAILABLE_ACTIONS
        )
    if session.context == ("market", "live-unavailable"):
        return LIVE_MARKET_UNAVAILABLE_ACTIONS
    if session.context == ("market",) and (
        session.market.query
        and session.market.catalog_setup_plan is not None
        and session.market.catalog_setup_plan.availability
        in {"usable", "partially_usable"}
    ):
        return (RESUME_MARKET_SEARCH_ACTION, *SECTION_ACTIONS["market"])
    if session.context == ("account", "selected"):
        return RESOURCE_ACCOUNT_ACTIONS
    if session.context == ("account", "order-segments"):
        record = session.account.selected
        return order_segment_actions(record) if record is not None else ()
    if session.context == ("account", "orders"):
        return ACCOUNT_ORDER_ACTIONS
    if session.context == ("account", "transfer-result"):
        return TRANSFER_RESULT_ACTIONS
    if session.context[:2] == ("operations", "service"):
        return service_actions(session.operations.selected_service_status)
    if session.context[:2] == ("operations", "service-logs"):
        return LOG_FOLLOW_ACTIONS
    if session.context[:2] == ("operations", "support"):
        return SUPPORT_ACTIONS
    if session.context == ("resources", "selected"):
        return resource_detail_actions(session.resources.kind)
    if session.context == ("resources", "setup"):
        return ()
    if session.context == ("resources", "ai-models"):
        return AI_MODEL_ACTIONS
    if (
        len(session.context) == 2
        and session.context[0] == "resources"
        and session.context[1] in RESOURCE_LABELS
    ):
        kind = session.context[1]
        if session.visible_records:
            return tuple(
                ActionItem(
                    str(index),
                    record.label,
                    record.description,
                    str(index),
                )
                for index, record in enumerate(session.visible_records, 1)
            )
        label = RESOURCE_LABELS[kind]
        return (ActionItem("new", f"添加{label}", "启动安全的单输入配置向导", "new"),)
    if session.context == ("research", "data"):
        return RESEARCH_DATA_ACTIONS
    if session.context == ("research", "research"):
        return RESEARCH_WORKFLOW_ACTIONS
    if session.context == ("strategy", "selected"):
        return STRATEGY_LAUNCH_ACTIONS
    if session.context == ("strategy", "instance"):
        return STRATEGY_INSTANCE_ACTIONS
    if session.context == ("strategy", "attach"):
        return STRATEGY_ATTACH_ACTIONS
    if session.context == ("strategy", "timeline"):
        return STRATEGY_TIMELINE_ACTIONS
    if session.context == ("strategy", "execution"):
        return STRATEGY_EXECUTION_ACTIONS
    if session.context == ("strategy", "market"):
        return STRATEGY_MARKET_ACTIONS
    if session.context == ("strategy", "setup"):
        return ()
    if len(session.context) > 1 and session.visible_records:
        return tuple(
            ActionItem(
                str(index),
                record.label,
                record.description,
                str(index),
            )
            for index, record in enumerate(session.visible_records, 1)
        )
    section = next(iter(session.context), None)
    return SECTION_ACTIONS.get(section, ()) if section is not None else ()


def context_label(context: tuple[str, ...], root_label: str = "首页") -> str:
    if not context:
        return root_label
    if context == ("project",):
        return f"{root_label} / 项目管理"
    parts = [root_label, SECTION_LABELS.get(context[0], context[0])]
    if context == ("account", "accounts"):
        return " / ".join(parts)
    if len(context) > 1:
        labels: Mapping[tuple[str, ...], str] = {
            ("market", "selected"): "已选标的",
            ("market", "providers"): "选择数据源",
            ("market", "missing"): "未找到标的",
            ("market", "catalog-exchange"): "选择交易所",
            ("market", "catalog-instrument"): "选择品种",
            ("market", "catalog-setup"): "准备标的目录",
            ("market", "live"): "我的实时行情",
            ("market", "live-unavailable"): "实时行情不可用",
            ("account", "selected"): "已选账户",
            ("account", "order-segments"): "订单管理 / 选择交易分区",
            ("account", "orders"): "订单管理",
            ("account", "transfer-result"): "资金划转",
            ("market", "workspace-market-results"): "选择市场",
            ("market", "workspace-subscriptions"): "退出当前会话行情",
            ("market", "workspace-subscription-content"): "选择行情内容",
            ("market", "workspace-snapshot-kind"): "选择快照内容",
            ("market", "workspace-timeframe"): "选择 K 线周期",
            ("market", "workspace-providers"): "选择行情来源",
            ("reference", "selected"): "已选目录记录",
            ("reference", "instrument-types"): "选择合约类型",
            ("operations", "services"): "项目共享服务",
            ("operations", "instances"): "活动运行实例",
            ("operations", "supports"): "支撑进程",
            ("operations", "service"): "服务操作",
            ("operations", "overview"): "运行概览",
            ("resources", "selected"): "已选运行资源",
            ("resources", "model-chat"): "模型对话",
            ("resources", "setup"): "配置向导",
            ("resources", "ai-models"): "AI 模型",
            ("research", "data"): "数据准备",
            ("research", "research"): "研究流程",
            ("strategy", "launches"): "运行方案",
            ("strategy", "selected"): "已选运行方案",
            ("strategy", "instances"): "运行实例",
            ("strategy", "instance"): "已选实例",
            ("strategy", "components"): "实例组件",
            ("strategy", "attach"): "跟随输出",
            ("strategy", "timeline"): "实例时间线",
            ("strategy", "execution"): "Execution Server",
            ("strategy", "market"): "Market 组件",
            ("strategy", "setup"): "配置向导",
        }
        parts.append(labels.get(context, "查询结果"))
        if len(context) == 3 and context[:2] == ("operations", "service"):
            parts[-1] = service_display_name(context[2])
        elif len(context) == 3 and context[:2] == (
            "operations",
            "service-logs",
        ):
            parts[-1] = f"{service_display_name(context[2])} / 实时日志"
        elif len(context) == 3 and context[:2] == ("operations", "support"):
            parts[-1] = {
                "system-supervisor": "System Supervisor",
                "aeron": "Aeron",
            }.get(context[2], context[2])
    return " / ".join(parts)


def record_label(record: Any) -> str:
    if isinstance(record, Mapping):
        for name in ("launch_id", "component", "name", "id"):
            value = record.get(name)
            if value:
                return str(value)
    for name in ("venue_symbol", "symbol", "code", "name"):
        value = getattr(record, name, None)
        if value:
            return str(value)
    instrument = getattr(record, "instrument", None)
    if instrument is not None and getattr(instrument, "display_symbol", None):
        return str(instrument.display_symbol)
    return str(getattr(record, "id", record))


def record_description(record: Any) -> str:
    if isinstance(record, Mapping):
        values = [
            str(record[name])
            for name in ("mode", "state", "status", "pid", "detail", "error")
            if record.get(name)
        ]
        return " · ".join(values) or "查看详情"
    values = []
    exchange_id = getattr(record, "exchange_id", None)
    if exchange_id:
        values.append(str(exchange_id).rsplit(":", 1)[-1])
    for name in ("name", "instrument_kind", "instrument_type", "asset_class", "status"):
        value = getattr(record, name, None)
        if value and str(value) not in values:
            values.append(str(value))
    return " · ".join(values) or str(getattr(record, "id", "查看详情"))


def menu_renderable(context: str, items: tuple[ActionItem, ...]) -> RenderableType:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(style="bold")
    table.add_column(style="dim")
    for item in items:
        table.add_row(display_shortcut(item.shortcut), item.label, item.description)
    return Panel(table, title=context, border_style="cyan")


def project_summary(state: Any) -> RenderableType:
    """Describe the global project gate without exposing raw configuration."""

    table = Table.grid(padding=(0, 3))
    table.add_column(style="dim", no_wrap=True)
    table.add_column()
    if state.owner is None:
        table.add_row("当前项目", "尚未打开")
        table.add_row("下一步", "打开已有项目或创建新项目")
        return table
    table.add_row("当前项目", state.workspace_id)
    table.add_row("项目路径", str(state.project_root or state.workspace_arg or "—"))
    table.add_row("状态", "项目已打开")
    return table


def display_shortcut(value: str | None) -> str:
    if value is None or value.isdecimal():
        return value or ""
    return f"/{value}"


__all__ = [
    "action_id",
    "context_items",
    "context_label",
    "display_shortcut",
    "go_back",
    "menu_renderable",
    "project_summary",
    "record_description",
    "record_label",
]
