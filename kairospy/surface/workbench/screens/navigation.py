"""Presentation-only navigation rules for the single Workbench screen."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rich.console import RenderableType
from rich.panel import Panel
from rich.table import Table

from ..widgets import ActionItem
from .guided.account import ACCOUNT_ACTIONS as RESOURCE_ACCOUNT_ACTIONS
from .guided.business import actions as business_actions
from .guided.catalog import HOME_ACTIONS, SECTION_ACTIONS, SECTION_LABELS
from .guided.execution import EXECUTION_ACTIONS as STRATEGY_EXECUTION_ACTIONS
from .guided.launch_market import MARKET_COMPONENT_ACTIONS as STRATEGY_MARKET_ACTIONS
from .guided.models import GuidedSession
from .guided.operations import (
    BUSINESS_ACTIONS as OPERATIONS_BUSINESS_ACTIONS,
    CONFIG_ACTIONS as OPERATIONS_CONFIG_ACTIONS,
    PROFILE_ACTIONS as OPERATIONS_PROFILE_ACTIONS,
    PROJECT_ACTIONS as OPERATIONS_PROJECT_ACTIONS,
    SERVICE_ACTIONS as OPERATIONS_SERVICE_ACTIONS,
)
from .guided.orders import ORDER_ACTIONS as ACCOUNT_ORDER_ACTIONS
from .guided.research import (
    DATA_ACTIONS as RESEARCH_DATA_ACTIONS,
    RESEARCH_ACTIONS as RESEARCH_WORKFLOW_ACTIONS,
)
from .guided.resources import detail_actions as resource_detail_actions
from .guided.resource_rendering import RESOURCE_LABELS, identity, record_summary
from .guided.strategy import (
    ATTACH_ACTIONS as STRATEGY_ATTACH_ACTIONS,
    INSTANCE_ACTIONS as STRATEGY_INSTANCE_ACTIONS,
    LAUNCH_ACTIONS as STRATEGY_LAUNCH_ACTIONS,
    TIMELINE_ACTIONS as STRATEGY_TIMELINE_ACTIONS,
)
from .guided.workspace_market import WORKSPACE_MARKET_ACTIONS
from .guided.market import (
    MARKET_CONTROL_ACTIONS,
    provider_actions as market_provider_actions,
    selected_market_actions,
)


def go_back(session: GuidedSession) -> bool:
    """Move to the presentation parent and report whether a move occurred."""

    if not session.context:
        return False
    if session.context == ("market", "providers"):
        session.context = ("market", "selected")
        session.visible_records = session.market.records
    elif session.context == ("market", "connected"):
        session.enter("market")
    elif session.context == ("market", "selected"):
        session.context = (
            ("market", "results") if session.market.records else ("market",)
        )
        session.visible_records = session.market.records
    elif session.context == ("reference", "selected"):
        kind = session.reference.kind
        session.context = (
            ("reference", kind)
            if kind is not None and session.visible_records
            else ("reference",)
        )
    elif session.context == ("operations", "service"):
        session.context = ("operations", "services")
    elif session.context == ("operations", "profiles"):
        session.context = ("operations", "config")
    elif len(session.context) == 3 and session.context[:2] == (
        "operations",
        "business",
    ):
        session.context = ("operations", "business")
    elif len(session.context) > 1 and session.context[0] == "operations":
        session.enter("operations")
    elif session.context == ("resources", "selected"):
        kind = session.resources.kind
        session.context = (
            ("resources", kind)
            if kind is not None and session.visible_records
            else ("resources",)
        )
    elif session.context == ("resources", "account-operations"):
        session.context = ("resources", "selected")
    elif session.context == ("resources", "account-orders"):
        session.context = ("resources", "account-operations")
    elif len(session.context) > 1 and session.context[0] == "resources":
        session.enter("resources")
    elif len(session.context) > 1 and session.context[0] == "research":
        session.enter("research")
    elif session.context == ("strategy", "selected"):
        session.context = (
            ("strategy", "launches")
            if session.strategy.launch_records
            else ("strategy",)
        )
        session.visible_records = session.strategy.launch_records
    elif session.context in {("strategy", "attach"), ("strategy", "instances")}:
        if session.context == ("strategy", "attach"):
            session.strategy.attach_paused = True
        session.context = ("strategy", "selected")
        session.visible_records = session.strategy.launch_records
    elif session.context in {
        ("strategy", "instance"),
        ("strategy", "components"),
        ("strategy", "timeline"),
        ("strategy", "execution"),
        ("strategy", "market"),
    }:
        if session.context in {
            ("strategy", "components"),
            ("strategy", "timeline"),
        }:
            session.context = ("strategy", "instance")
            session.visible_records = session.strategy.instance_records
        elif session.context in {
            ("strategy", "execution"),
            ("strategy", "market"),
        }:
            session.context = ("strategy", "components")
            session.visible_records = session.strategy.component_records
        else:
            session.context = ("strategy", "instances")
            session.visible_records = session.strategy.instance_records
    elif len(session.context) > 1 and session.context[0] == "strategy":
        session.enter("strategy")
    elif len(session.context) > 1 and session.context[0] == "reference":
        if (
            session.context[1] == "instruments"
            and session.reference.instrument_type is not None
        ):
            session.context = ("reference", "instrument-types")
            session.visible_records = ()
        else:
            session.enter("reference")
    elif len(session.context) > 1:
        session.enter(session.context[0])
    else:
        session.home()
    return True


def action_id(items: tuple[ActionItem, ...], value: str) -> str | None:
    lowered = value.lower()
    for item in items:
        if lowered in {item.id.lower(), (item.shortcut or "").lower()}:
            return item.id
    return None


def context_items(session: GuidedSession, state: Any) -> tuple[ActionItem, ...]:
    if not session.context:
        return HOME_ACTIONS
    if session.context == ("market", "selected"):
        market = getattr(state, "selected_market", None)
        if market is None:
            return ()
        actions = selected_market_actions(market)
        if session.market.snapshot is not None:
            actions = (*actions, *MARKET_CONTROL_ACTIONS)
        return actions
    if session.context == ("market", "providers"):
        return market_provider_actions(session.market.routes)
    if session.context == ("market", "connected"):
        return WORKSPACE_MARKET_ACTIONS
    if session.context == ("operations", "project"):
        return OPERATIONS_PROJECT_ACTIONS
    if session.context == ("operations", "config"):
        return OPERATIONS_CONFIG_ACTIONS
    if session.context == ("operations", "business"):
        return OPERATIONS_BUSINESS_ACTIONS
    if session.context == ("operations", "profiles"):
        return OPERATIONS_PROFILE_ACTIONS
    if len(session.context) == 3 and session.context[:2] == ("operations", "business"):
        return business_actions(session.context[2])
    if session.context == ("operations", "service"):
        return OPERATIONS_SERVICE_ACTIONS
    if session.context == ("resources", "selected"):
        return resource_detail_actions(session.resources.kind)
    if session.context == ("resources", "account-operations"):
        return RESOURCE_ACCOUNT_ACTIONS
    if session.context == ("resources", "account-orders"):
        return ACCOUNT_ORDER_ACTIONS
    if session.context == ("resources", "setup"):
        return ()
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
                    identity(kind, record),
                    record_summary(kind, record),
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
                record_label(record),
                record_description(record),
                str(index),
            )
            for index, record in enumerate(session.visible_records, 1)
        )
    section = next(iter(session.context), None)
    return SECTION_ACTIONS.get(section, ()) if section is not None else ()


def context_label(context: tuple[str, ...]) -> str:
    if not context:
        return "首页"
    parts = ["首页", SECTION_LABELS.get(context[0], context[0])]
    if len(context) > 1:
        labels: Mapping[tuple[str, ...], str] = {
            ("market", "selected"): "已选标的",
            ("market", "providers"): "选择数据源",
            ("market", "connected"): "运行中 Market",
            ("reference", "selected"): "已选目录记录",
            ("reference", "instrument-types"): "选择合约类型",
            ("operations", "project"): "项目工作区",
            ("operations", "services"): "系统服务",
            ("operations", "service"): "服务操作",
            ("operations", "config"): "高级配置",
            ("operations", "business"): "业务工具",
            ("operations", "profiles"): "配置 Profiles",
            ("resources", "selected"): "已选运行资源",
            ("resources", "account-operations"): "账户运行查询",
            ("resources", "account-orders"): "订单管理",
            ("resources", "setup"): "配置向导",
            ("research", "data"): "数据准备",
            ("research", "research"): "研究流程",
            ("strategy", "launches"): "Launch 列表",
            ("strategy", "selected"): "已选 Launch",
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
        if len(context) == 3 and context[:2] == ("operations", "business"):
            parts[-1] = {
                "risk": "Risk",
                "capital": "Capital",
                "integration": "Provider 集成",
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
    "record_description",
    "record_label",
]
