"""Presentation-only navigation rules for the single Workbench screen."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from rich.console import RenderableType
from rich.panel import Panel
from rich.table import Table

from ..widgets import ActionItem, ChoiceInteraction
from .flows.resources.account_actions import ACCOUNT_ACTIONS as RESOURCE_ACCOUNT_ACTIONS
from .flows.resources.account_transfers import TRANSFER_RESULT_ACTIONS
from .catalog import AI_MODEL_ACTIONS, HOME_ACTIONS, SECTION_ACTIONS, SECTION_LABELS
from .flows.launch.execution_actions import (
    EXECUTION_ACTIONS as STRATEGY_EXECUTION_ACTIONS,
)
from .flows.launch.market_actions import (
    MARKET_COMPONENT_ACTIONS as STRATEGY_MARKET_ACTIONS,
)
from .session import GuidedSession
from .flows.operations.actions import (
    project_actions,
)
from .flows.operations.views import (
    LOG_FOLLOW_ACTIONS,
    SUPPORT_ACTIONS,
    service_actions,
    service_display_name,
)
from .flows.launch.orders import (
    ORDER_ACTIONS as ACCOUNT_ORDER_ACTIONS,
    order_segment_actions,
    order_segments,
)
from .flows.research.actions import (
    DATA_ACTIONS as RESEARCH_DATA_ACTIONS,
    RESEARCH_ACTIONS as RESEARCH_WORKFLOW_ACTIONS,
)
from .flows.resources.actions import detail_actions as resource_detail_actions
from .flows.resources.views import RESOURCE_LABELS, identity, record_summary
from .flows.launch.actions import (
    ATTACH_ACTIONS as STRATEGY_ATTACH_ACTIONS,
    INSTANCE_ACTIONS as STRATEGY_INSTANCE_ACTIONS,
    LAUNCH_ACTIONS as STRATEGY_LAUNCH_ACTIONS,
    TIMELINE_ACTIONS as STRATEGY_TIMELINE_ACTIONS,
)
from .flows.market.workspace import WORKSPACE_MARKET_ACTIONS
from .flows.market.actions import (
    MARKET_CONTROL_ACTIONS,
    provider_actions as market_provider_actions,
    selected_market_actions,
)
from .selection import SelectionRecord, selection_records


def _visible(records: tuple[Any, ...]) -> tuple[SelectionRecord, ...]:
    return selection_records(
        records,
        label=record_label,
        description=record_description,
    )


def go_back(session: GuidedSession) -> bool:
    """Move to the presentation parent and report whether a move occurred."""

    if not session.context:
        return False
    if session.context == ("project",):
        session.home()
    elif session.context == ("operations", "overview"):
        session.home()
    elif session.context in {
        ("operations", "services"),
        ("operations", "instances"),
        ("operations", "supports"),
    }:
        session.context = ("operations", "overview")
        session.visible_records = session.operations.group_records
    elif session.context == ("market", "providers"):
        session.context = ("market", "selected")
        session.visible_records = _visible(session.market.records)
    elif session.context == ("market", "connected"):
        session.enter("market")
    elif session.context == ("market", "selected"):
        session.context = (
            ("market", "results") if session.market.records else ("market",)
        )
        session.visible_records = _visible(session.market.records)
    elif session.context == ("reference", "selected"):
        kind = session.reference.kind
        session.context = (
            ("reference", kind)
            if kind is not None and session.visible_records
            else ("reference",)
        )
    elif session.context[:2] == ("operations", "service-logs"):
        component = session.operations.selected_service
        session.context = (
            ("operations", "service", component)
            if component is not None
            else ("operations", "services")
        )
    elif session.context[:2] == ("operations", "service"):
        session.context = ("operations", "services")
        session.visible_records = tuple(
            record
            for record in session.operations.inventory_records
            if isinstance(record.value, Mapping)
            and record.value.get("kind") == "service"
        )
    elif session.context[:2] == ("operations", "support"):
        session.context = ("operations", "supports")
        session.visible_records = tuple(
            record
            for record in session.operations.inventory_records
            if isinstance(record.value, Mapping)
            and record.value.get("kind") == "support"
        )
    elif len(session.context) > 1 and session.context[0] == "operations":
        session.enter("operations")
    elif session.context == ("resources", "model-chat"):
        session.context = ("resources", "selected")
        session.resources.action = None
    elif session.context == ("resources", "selected"):
        kind = session.resources.kind
        session.context = (
            ("resources", kind)
            if kind is not None and session.visible_records
            else ("resources",)
        )
    elif session.context == ("resources", "account-operations"):
        kind = session.resources.kind
        session.resources.selected = None
        session.context = (
            ("resources", kind)
            if kind is not None and session.visible_records
            else ("resources",)
        )
    elif session.context == ("resources", "account-orders"):
        session.account.order_prompt = None
        record = session.resources.selected or {}
        if len(order_segments(record)) > 1:
            session.context = ("resources", "account-order-segments")
        else:
            session.account.selected_segment = None
            session.context = ("resources", "account-operations")
    elif session.context == ("resources", "account-order-segments"):
        session.account.reset()
        session.context = ("resources", "account-operations")
    elif session.context in {
        ("resources", "models"),
        ("resources", "model_endpoints"),
    }:
        session.context = ("resources", "ai-models")
        session.resources.kind = None
        session.visible_records = ()
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
        session.visible_records = _visible(session.strategy.launch_records)
    elif session.context in {("strategy", "attach"), ("strategy", "instances")}:
        if session.context == ("strategy", "attach"):
            session.strategy.attach_paused = True
        session.context = ("strategy", "selected")
        session.visible_records = _visible(session.strategy.launch_records)
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
            session.visible_records = _visible(session.strategy.instance_records)
        elif session.context in {
            ("strategy", "execution"),
            ("strategy", "market"),
        }:
            session.context = ("strategy", "components")
            session.visible_records = _visible(session.strategy.component_records)
        else:
            if session.strategy.instance_entered_from_operations:
                session.context = ("operations", "instances")
                session.visible_records = tuple(
                    record
                    for record in session.operations.inventory_records
                    if isinstance(record.value, Mapping)
                    and record.value.get("kind") == "run-instance"
                )
                session.strategy.instance_entered_from_operations = False
            else:
                session.context = ("strategy", "instances")
                session.visible_records = _visible(session.strategy.instance_records)
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


def back_targets(session: GuidedSession) -> tuple[tuple[str, ...], ...]:
    """Return every reachable presentation parent, nearest first."""

    # Semantic parents are not always tuple prefixes. Walking a detached
    # session keeps the picker governed by the same rules as real navigation.
    probe = deepcopy(session)
    probe.interaction = ChoiceInteraction()
    probe.suspended_interaction = None
    targets: list[tuple[str, ...]] = []
    while go_back(probe):
        targets.append(probe.context)
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
    if session.context == ("market", "connected"):
        return WORKSPACE_MARKET_ACTIONS
    if session.context[:2] == ("operations", "service"):
        return service_actions(session.operations.selected_service_status)
    if session.context[:2] == ("operations", "service-logs"):
        return LOG_FOLLOW_ACTIONS
    if session.context[:2] == ("operations", "support"):
        return SUPPORT_ACTIONS
    if session.context == ("resources", "selected"):
        return resource_detail_actions(session.resources.kind)
    if session.context == ("resources", "account-operations"):
        return RESOURCE_ACCOUNT_ACTIONS
    if session.context == ("resources", "account-order-segments"):
        record = session.resources.selected
        return order_segment_actions(record) if record is not None else ()
    if session.context == ("resources", "account-orders"):
        return ACCOUNT_ORDER_ACTIONS
    if session.context == ("resources", "account-transfer-result"):
        return TRANSFER_RESULT_ACTIONS
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
    if len(context) > 1:
        labels: Mapping[tuple[str, ...], str] = {
            ("market", "selected"): "已选标的",
            ("market", "providers"): "选择数据源",
            ("market", "connected"): "运行中 Market",
            ("reference", "selected"): "已选目录记录",
            ("reference", "instrument-types"): "选择合约类型",
            ("operations", "services"): "项目共享服务",
            ("operations", "instances"): "活动运行实例",
            ("operations", "supports"): "支撑进程",
            ("operations", "service"): "服务操作",
            ("operations", "overview"): "运行概览",
            ("resources", "selected"): "已选运行资源",
            ("resources", "model-chat"): "模型对话",
            ("resources", "account-operations"): "交易账户",
            ("resources", "account-order-segments"): "订单管理 / 选择交易分区",
            ("resources", "account-orders"): "订单管理",
            ("resources", "account-transfer-result"): "资金划转",
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
