"""Workbench 用户任务之间的进入、返回与可用操作。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from rich.console import RenderableType
from rich.panel import Panel
from rich.table import Table

from ...widgets import ActionItem, ChoiceInteraction
from ..flows.account.actions import (
    ACCOUNT_ACTIONS as RESOURCE_ACCOUNT_ACTIONS,
    ACCOUNT_FUNDS_ACTIONS,
)
from ..flows.account.transfers import TRANSFER_RESULT_ACTIONS
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
from .identity import NavigationContext, Routes, Section, belongs_to, route, starts_with
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
from ..flows.reference.actions import CatalogSetupPlanView, source_actions
from ..flows.launch.actions import (
    ATTACH_ACTIONS as STRATEGY_ATTACH_ACTIONS,
    LAUNCH_ACTIONS as STRATEGY_LAUNCH_ACTIONS,
    TIMELINE_ACTIONS as STRATEGY_TIMELINE_ACTIONS,
    instance_actions as strategy_instance_actions,
    readiness_actions as strategy_readiness_actions,
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


@dataclass(frozen=True, slots=True)
class CommandContextView:
    """The stable task anchor shown above the single command input."""

    segments: tuple[str, ...]


_RESOURCE_TASK_LABELS = {
    "accounts": "交易账户",
    "data": "行情连接",
    "model_endpoints": "模型服务",
    "models": "AI 模型",
    "notifications": "通知连接",
}


def command_context(session: GuidedSession) -> CommandContextView:
    """Project navigation state into a compact, task-oriented path.

    Visit history remains owned by ``navigation_stack`` and is exposed through
    ``/back``.  This view instead answers: what task, problem, and object
    is the user acting on now?
    """

    launch = session.strategy.selected_record
    launch_id = (
        str(launch.get("launch_id")) if launch and launch.get("launch_id") else None
    )
    if session.context == Routes.STRATEGY_READINESS and launch_id:
        return CommandContextView((launch_id, "运行条件"))

    if launch_id and belongs_to(session.context, Section.STRATEGY):
        page = {
            Routes.STRATEGY_SELECTED: "运行方案",
            Routes.STRATEGY_INSTANCES: "运行实例",
            Routes.STRATEGY_INSTANCE: "运行实例",
            Routes.STRATEGY_COMPONENTS: "实例组件",
            Routes.STRATEGY_ATTACH: "跟随输出",
            Routes.STRATEGY_TIMELINE: "实例时间线",
            Routes.STRATEGY_EXECUTION: "Execution Server",
            Routes.STRATEGY_MARKET: "Market 组件",
            Routes.STRATEGY_SETUP: "配置向导",
        }.get(session.context)
        if page:
            return CommandContextView((launch_id, page))

    if belongs_to(session.context, Section.RESOURCES):
        kind = session.resources.kind or (
            session.resources.wizard.kind
            if session.resources.wizard is not None
            else None
        )
        if kind is None:
            return CommandContextView((session.root_label, "连接与配置"))
        task = _RESOURCE_TASK_LABELS.get(kind or "", "连接与配置")
        history = {frame.context for frame in session.navigation_stack}
        anchor = "连接与配置"
        if Routes.STRATEGY_READINESS in history and launch_id:
            anchor = launch_id
        elif Routes.MARKET_CATALOG_SETUP in history:
            anchor = session.market.query or "当前标的"

        subject: str | None = None
        selected = session.resources.selected
        if (
            session.context == Routes.RESOURCES_SELECTED
            and kind
            and selected is not None
        ):
            subject = identity(kind, selected)
        wizard = session.resources.wizard
        if session.context == Routes.RESOURCES_SETUP and wizard is not None:
            provider = (
                wizard.answers.get("data-provider")
                or wizard.answers.get("account-provider")
                or wizard.answers.get("model-provider")
                or wizard.answers.get("notification-provider")
            )
            if provider:
                subject = _provider_label(str(provider))
            elif wizard.record:
                subject = identity(wizard.kind, wizard.record)
            elif wizard.generated_id:
                subject = wizard.generated_id
        segments = (anchor, task, subject) if subject else (anchor, task)
        return CommandContextView(tuple(part for part in segments if part))

    if session.context == Routes.MARKET_CATALOG_SETUP:
        anchor = session.market.query or "当前标的"
        return CommandContextView((anchor, "标的目录", "准备条件"))

    if (
        session.context == Routes.MARKET_SELECTED
        and session.market.selected is not None
    ):
        return CommandContextView(
            (
                record_label(session.market.selected),
                "行情",
                session.market.provider or "已选标的",
            )
        )

    account = session.account.selected
    if account is not None and belongs_to(session.context, Section.ACCOUNT):
        account_id = identity("accounts", account)
        if session.context == Routes.ACCOUNT_FUNDS:
            return CommandContextView((account_id, "资金与费率"))
        if session.context == Routes.ACCOUNT_ORDER_SEGMENTS:
            return CommandContextView((account_id, "订单管理", "选择交易分区"))
        if session.context == Routes.ACCOUNT_ORDERS:
            return CommandContextView(
                (account_id, "订单管理", session.account.selected_segment or "当前订单")
            )
        if session.context == Routes.ACCOUNT_TRANSFER_RESULT:
            return CommandContextView((account_id, "资金划转"))
        if session.context == Routes.ACCOUNT_SELECTED:
            return CommandContextView((account_id, "账户"))

    if (
        session.context == Routes.REFERENCE_SELECTED
        and session.reference.selected is not None
    ):
        return CommandContextView(
            (record_label(session.reference.selected), "标的目录", "目录记录")
        )

    label = context_label(session.context, session.root_label)
    parts = tuple(part.strip() for part in label.split(" / ") if part.strip())
    if len(parts) <= 3:
        return CommandContextView(parts)
    return CommandContextView((parts[0], parts[-2], parts[-1]))


def _provider_label(provider: str) -> str:
    return {
        "massive": "Massive",
        "binance": "Binance",
        "okx": "OKX",
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "openrouter": "OpenRouter",
        "telegram": "Telegram",
        "feishu": "飞书",
    }.get(provider.lower(), provider)


def _visible(records: tuple[Any, ...]) -> tuple[SelectionRecord, ...]:
    return selection_records(
        records,
        label=record_label,
        description=record_description,
    )


def _parent_context(
    session: GuidedSession, context: NavigationContext
) -> NavigationContext | None:
    """Return the semantic parent without copying or mutating session state."""

    if not context:
        return None
    if context in {Routes.PROJECT, Routes.OPERATIONS_OVERVIEW}:
        return Routes.HOME
    if context in {
        Routes.OPERATIONS_SERVICES,
        Routes.OPERATIONS_INSTANCES,
        Routes.OPERATIONS_SUPPORTS,
    }:
        return Routes.OPERATIONS_OVERVIEW
    if context == Routes.MARKET_PROVIDERS:
        return Routes.MARKET_SELECTED
    if context == Routes.ACCOUNT_LIST:
        return Routes.HOME
    if context == Routes.ACCOUNT_SELECTED:
        return Routes.ACCOUNT_LIST
    if context == Routes.ACCOUNT_FUNDS:
        return Routes.ACCOUNT_SELECTED
    if context == Routes.ACCOUNT_ORDERS:
        record = session.account.selected or {}
        return (
            Routes.ACCOUNT_ORDER_SEGMENTS
            if len(order_segments(record)) > 1
            else Routes.ACCOUNT_SELECTED
        )
    if context == Routes.ACCOUNT_ORDER_SEGMENTS:
        return Routes.ACCOUNT_SELECTED
    if context == Routes.ACCOUNT_TRANSFER_RESULT:
        return Routes.ACCOUNT_SELECTED
    if context in {
        Routes.MARKET_WORKSPACE_MARKET_RESULTS,
        Routes.MARKET_WORKSPACE_SUBSCRIPTIONS,
        Routes.MARKET_WORKSPACE_SUBSCRIPTION_CONTENT,
        Routes.MARKET_WORKSPACE_SNAPSHOT_KIND,
        Routes.MARKET_WORKSPACE_TIMEFRAME,
        Routes.MARKET_WORKSPACE_PROVIDERS,
    }:
        return Routes.MARKET_LIVE
    if context in {Routes.MARKET_LIVE_UNAVAILABLE, Routes.MARKET_LIVE}:
        return Routes.MARKET
    if context == Routes.MARKET_SELECTED:
        return Routes.MARKET_RESULTS if session.market.records else Routes.MARKET
    if context == Routes.REFERENCE:
        return Routes.MARKET
    if context == Routes.REFERENCE_SOURCE_SELECTED:
        return Routes.REFERENCE_SOURCES
    if context == Routes.REFERENCE_SOURCES:
        return Routes.REFERENCE
    if context == Routes.REFERENCE_SELECTED:
        kind = session.reference.kind
        return (
            route(Section.REFERENCE, kind)
            if kind is not None and session.visible_records
            else Routes.REFERENCE
        )
    if starts_with(context, Routes.OPERATIONS_SERVICE_LOGS):
        component = session.operations.selected_service
        return (
            (*Routes.OPERATIONS_SERVICE, component)
            if component is not None
            else Routes.OPERATIONS_SERVICES
        )
    if starts_with(context, Routes.OPERATIONS_SERVICE):
        return Routes.OPERATIONS_SERVICES
    if starts_with(context, Routes.OPERATIONS_SUPPORT):
        return Routes.OPERATIONS_SUPPORTS
    if len(context) > 1 and belongs_to(context, Section.OPERATIONS):
        return Routes.OPERATIONS
    if context == Routes.RESOURCES_MODEL_CHAT:
        return Routes.RESOURCES_SELECTED
    if context == Routes.RESOURCES_SELECTED:
        kind = session.resources.kind
        return (
            route(Section.RESOURCES, kind)
            if kind is not None and session.visible_records
            else Routes.RESOURCES
        )
    if context in {
        Routes.RESOURCES_MODELS,
        Routes.RESOURCES_MODEL_ENDPOINTS,
    }:
        return Routes.RESOURCES_AI_MODELS
    if len(context) > 1 and belongs_to(context, Section.RESOURCES):
        return Routes.RESOURCES
    if len(context) > 1 and belongs_to(context, Section.RESEARCH):
        return Routes.RESEARCH
    if context == Routes.STRATEGY_SELECTED:
        return (
            Routes.STRATEGY_LAUNCHES
            if session.strategy.launch_records
            else Routes.STRATEGY
        )
    if context == Routes.STRATEGY_READINESS:
        return Routes.STRATEGY_SELECTED
    if context == Routes.STRATEGY_ATTACH:
        return Routes.STRATEGY_INSTANCE
    if context == Routes.STRATEGY_INSTANCES:
        return Routes.STRATEGY_SELECTED
    if context in {Routes.STRATEGY_COMPONENTS, Routes.STRATEGY_TIMELINE}:
        return Routes.STRATEGY_INSTANCE
    if context in {Routes.STRATEGY_EXECUTION, Routes.STRATEGY_MARKET}:
        return Routes.STRATEGY_COMPONENTS
    if context == Routes.STRATEGY_INSTANCE:
        return (
            Routes.OPERATIONS_INSTANCES
            if session.strategy.instance_entered_from_operations
            else Routes.STRATEGY_INSTANCES
        )
    if len(context) > 1 and belongs_to(context, Section.STRATEGY):
        return Routes.STRATEGY
    if len(context) > 1 and belongs_to(context, Section.REFERENCE):
        if (
            context[1] == "instruments"
            and session.reference.instrument_type is not None
        ):
            return Routes.REFERENCE_INSTRUMENT_TYPES
        return Routes.REFERENCE
    if len(context) > 1:
        return (context[0],)
    return Routes.HOME


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
    if target == Routes.HOME:
        session.home()
        return True

    if source in {
        Routes.OPERATIONS_SERVICES,
        Routes.OPERATIONS_INSTANCES,
        Routes.OPERATIONS_SUPPORTS,
    }:
        session.visible_records = session.operations.group_records
    elif source == Routes.MARKET_PROVIDERS:
        session.visible_records = _visible(session.market.records)
    elif source in {
        Routes.MARKET_WORKSPACE_MARKET_RESULTS,
        Routes.MARKET_WORKSPACE_SUBSCRIPTIONS,
        Routes.MARKET_WORKSPACE_SUBSCRIPTION_CONTENT,
        Routes.MARKET_WORKSPACE_SNAPSHOT_KIND,
        Routes.MARKET_WORKSPACE_TIMEFRAME,
        Routes.MARKET_WORKSPACE_PROVIDERS,
    }:
        session.market.workspace_prompt = None
        session.visible_records = ()
    elif source == Routes.MARKET_LIVE:
        pass
    elif source == Routes.MARKET_SELECTED:
        session.visible_records = _visible(session.market.records)
    elif source == Routes.ACCOUNT_SELECTED:
        session.account.selected = None
        session.visible_records = selection_records(
            session.account.records,
            key=lambda record: identity("accounts", record),
            label=lambda record: identity("accounts", record),
            description=lambda record: record_summary("accounts", record),
        )
    elif source == Routes.ACCOUNT_ORDERS:
        session.account.order_prompt = None
        if target == Routes.ACCOUNT_SELECTED:
            session.account.selected_segment = None
    elif source == Routes.ACCOUNT_ORDER_SEGMENTS:
        session.account.selected_segment = None
        session.account.order_prompt = None
    elif source == Routes.ACCOUNT_TRANSFER_RESULT:
        session.account.transfer_prompt = None
    elif source == Routes.REFERENCE_SELECTED:
        pass
    elif starts_with(source, Routes.OPERATIONS_SERVICE_LOGS):
        pass
    elif starts_with(source, Routes.OPERATIONS_SERVICE) and not starts_with(
        source, Routes.OPERATIONS_SERVICE_LOGS
    ):
        session.visible_records = tuple(
            record
            for record in session.operations.inventory_records
            if isinstance(record.value, Mapping)
            and record.value.get("kind") == "service"
        )
    elif starts_with(source, Routes.OPERATIONS_SUPPORT):
        session.visible_records = tuple(
            record
            for record in session.operations.inventory_records
            if isinstance(record.value, Mapping)
            and record.value.get("kind") == "support"
        )
    elif len(source) > 1 and belongs_to(source, Section.OPERATIONS):
        pass
    elif source == Routes.RESOURCES_MODEL_CHAT:
        session.resources.action = None
    elif source == Routes.RESOURCES_SELECTED:
        pass
    elif source in {
        Routes.RESOURCES_MODELS,
        Routes.RESOURCES_MODEL_ENDPOINTS,
    }:
        session.resources.kind = None
        session.visible_records = ()
    elif len(source) > 1 and belongs_to(source, Section.RESOURCES):
        pass
    elif len(source) > 1 and belongs_to(source, Section.RESEARCH):
        pass
    elif source == Routes.STRATEGY_SELECTED:
        session.visible_records = _visible(session.strategy.launch_records)
    elif source == Routes.STRATEGY_ATTACH:
        session.strategy.attach_paused = True
        session.visible_records = ()
    elif source == Routes.STRATEGY_INSTANCES:
        session.visible_records = _visible(session.strategy.launch_records)
    elif source in {
        Routes.STRATEGY_INSTANCE,
        Routes.STRATEGY_COMPONENTS,
        Routes.STRATEGY_TIMELINE,
        Routes.STRATEGY_EXECUTION,
        Routes.STRATEGY_MARKET,
    }:
        if source in {
            Routes.STRATEGY_COMPONENTS,
            Routes.STRATEGY_TIMELINE,
        }:
            session.visible_records = _visible(session.strategy.instance_records)
        elif source in {
            Routes.STRATEGY_EXECUTION,
            Routes.STRATEGY_MARKET,
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
    elif len(source) > 1 and belongs_to(source, Section.STRATEGY):
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
            ActionItem("change", "重新选择市场或服务", "修改要准备的目录范围", "2"),
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
        ActionItem("change", "重新选择市场或服务", "修改要准备的目录范围", "4")
    )
    return tuple(actions)


def _catalog_reference_recovery_actions(
    recovery: str | None,
) -> tuple[ActionItem, ...]:
    actions: list[ActionItem] = []
    if recovery == "repair-start":
        actions.append(
            ActionItem(
                "recover-reference",
                "清理并启动 Reference",
                "清理确认失效的运行资源，启动服务后继续检查",
                "1",
            )
        )
    elif recovery == "start":
        actions.append(
            ActionItem(
                "recover-reference",
                "启动 Reference 并继续",
                "启动项目共享标的服务，然后继续检查准备条件",
                "1",
            )
        )
    offset = len(actions)
    actions.extend(
        (
            ActionItem(
                "check",
                "重新检查",
                "刷新服务状态并继续检查准备条件",
                str(offset + 1),
            ),
            ActionItem(
                "reference-details",
                "查看服务详细状态",
                "前往运行中心查看状态、日志和技术诊断",
                str(offset + 2),
            ),
            ActionItem(
                "change",
                "重新选择市场或服务",
                "修改要准备的目录范围",
                str(offset + 3),
            ),
        )
    )
    return tuple(actions)


def context_items(session: GuidedSession, state: Any) -> tuple[ActionItem, ...]:
    if not session.context:
        return HOME_ACTIONS
    if session.context == Routes.PROJECT:
        return project_actions(has_project=state.owner is not None)
    if session.context == Routes.MARKET_SELECTED:
        market = session.market.selected
        if market is None:
            return ()
        actions = selected_market_actions(market)
        if session.market.snapshot is not None:
            actions = (*actions, *MARKET_CONTROL_ACTIONS)
        return tuple(
            ActionItem(item.id, item.label, item.description, str(index))
            for index, item in enumerate(actions, 1)
        )
    if session.context == Routes.MARKET_PROVIDERS:
        return market_provider_actions(session.market.routes)
    if session.context == Routes.MARKET_MISSING:
        return MISSING_MARKET_ACTIONS
    if session.context == Routes.MARKET_CATALOG_EXCHANGE:
        return CATALOG_EXCHANGE_ACTIONS
    if session.context == Routes.MARKET_CATALOG_INSTRUMENT:
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
    if session.context == Routes.MARKET_CATALOG_SETUP:
        if session.market.catalog_setup_reference_issue is not None:
            return _catalog_reference_recovery_actions(
                session.market.catalog_setup_reference_recovery
            )
        return _catalog_setup_actions(session.market.catalog_setup_plan)
    if session.context == Routes.MARKET_LIVE:
        return (
            LIVE_MARKET_ACTIONS
            if live_market_available(state)
            else LIVE_MARKET_UNAVAILABLE_ACTIONS
        )
    if session.context == Routes.MARKET_LIVE_UNAVAILABLE:
        return LIVE_MARKET_UNAVAILABLE_ACTIONS
    if session.context == Routes.MARKET and (
        session.market.query
        and session.market.catalog_setup_plan is not None
        and session.market.catalog_setup_plan.availability
        in {"usable", "partially_usable"}
    ):
        return tuple(
            ActionItem(item.id, item.label, item.description, str(index))
            for index, item in enumerate(
                (RESUME_MARKET_SEARCH_ACTION, *SECTION_ACTIONS[Section.MARKET]), 1
            )
        )
    if session.context == Routes.ACCOUNT_SELECTED:
        return RESOURCE_ACCOUNT_ACTIONS
    if session.context == Routes.ACCOUNT_FUNDS:
        return ACCOUNT_FUNDS_ACTIONS
    if session.context == Routes.ACCOUNT_ORDER_SEGMENTS:
        record = session.account.selected
        return order_segment_actions(record) if record is not None else ()
    if session.context == Routes.ACCOUNT_ORDERS:
        return ACCOUNT_ORDER_ACTIONS
    if session.context == Routes.ACCOUNT_TRANSFER_RESULT:
        return TRANSFER_RESULT_ACTIONS
    if starts_with(session.context, Routes.OPERATIONS_SERVICE):
        return service_actions(session.operations.selected_service_status)
    if starts_with(session.context, Routes.OPERATIONS_SERVICE_LOGS):
        return LOG_FOLLOW_ACTIONS
    if starts_with(session.context, Routes.OPERATIONS_SUPPORT):
        return SUPPORT_ACTIONS
    if session.context == Routes.RESOURCES_SELECTED:
        return resource_detail_actions(session.resources.kind)
    if session.context == Routes.REFERENCE_SOURCE_SELECTED:
        return source_actions(session.reference.selected_source)
    if session.context == Routes.REFERENCE_SOURCES:
        records = tuple(
            ActionItem(str(index), record.label, record.description, str(index))
            for index, record in enumerate(session.visible_records, 1)
        )
        return (
            *records,
            ActionItem(
                "add",
                "添加目录来源",
                "选择要准备的市场或交易服务",
                str(len(records) + 1),
            ),
        )
    if session.context == Routes.RESOURCES_SETUP:
        return ()
    if session.context == Routes.RESOURCES_AI_MODELS:
        return AI_MODEL_ACTIONS
    if (
        len(session.context) == 2
        and belongs_to(session.context, Section.RESOURCES)
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
    if session.context == Routes.RESEARCH_DATA:
        return RESEARCH_DATA_ACTIONS
    if session.context == Routes.RESEARCH_WORKFLOW:
        return RESEARCH_WORKFLOW_ACTIONS
    if session.context == Routes.STRATEGY_SELECTED:
        return STRATEGY_LAUNCH_ACTIONS
    if session.context == Routes.STRATEGY_READINESS:
        return strategy_readiness_actions(session.strategy.readiness)
    if session.context == Routes.STRATEGY_INSTANCE:
        return strategy_instance_actions(session.strategy.selected_record)
    if session.context == Routes.STRATEGY_ATTACH:
        return STRATEGY_ATTACH_ACTIONS
    if session.context == Routes.STRATEGY_TIMELINE:
        return STRATEGY_TIMELINE_ACTIONS
    if session.context == Routes.STRATEGY_EXECUTION:
        return STRATEGY_EXECUTION_ACTIONS
    if session.context == Routes.STRATEGY_MARKET:
        return STRATEGY_MARKET_ACTIONS
    if session.context == Routes.STRATEGY_SETUP:
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
    section = Section(session.context[0]) if session.context else None
    return SECTION_ACTIONS.get(section, ()) if section is not None else ()


def context_label(context: NavigationContext, root_label: str = "首页") -> str:
    if not context:
        return root_label
    if context == Routes.PROJECT:
        return f"{root_label} / 项目管理"
    section = Section(context[0])
    parts = [root_label, SECTION_LABELS.get(section, section.value)]
    if context == Routes.ACCOUNT_LIST:
        return " / ".join(parts)
    if len(context) > 1:
        labels: Mapping[NavigationContext, str] = {
            Routes.MARKET_SELECTED: "已选标的",
            Routes.MARKET_PROVIDERS: "选择数据源",
            Routes.MARKET_MISSING: "未找到标的",
            Routes.MARKET_CATALOG_EXCHANGE: "选择市场或交易服务",
            Routes.MARKET_CATALOG_INSTRUMENT: "选择品种",
            Routes.MARKET_CATALOG_SETUP: "准备标的目录",
            Routes.MARKET_LIVE: "我的实时行情",
            Routes.MARKET_LIVE_UNAVAILABLE: "实时行情不可用",
            Routes.REFERENCE_SOURCES: "管理目录来源",
            Routes.REFERENCE_SOURCE_SELECTED: "已选目录来源",
            Routes.ACCOUNT_SELECTED: "已选账户",
            Routes.ACCOUNT_FUNDS: "资金、理财与费率",
            Routes.ACCOUNT_ORDER_SEGMENTS: "订单管理 / 选择交易分区",
            Routes.ACCOUNT_ORDERS: "订单管理",
            Routes.ACCOUNT_TRANSFER_RESULT: "资金划转",
            Routes.MARKET_WORKSPACE_MARKET_RESULTS: "选择市场",
            Routes.MARKET_WORKSPACE_SUBSCRIPTIONS: "退出当前会话行情",
            Routes.MARKET_WORKSPACE_SUBSCRIPTION_CONTENT: "选择行情内容",
            Routes.MARKET_WORKSPACE_SNAPSHOT_KIND: "选择快照内容",
            Routes.MARKET_WORKSPACE_TIMEFRAME: "选择 K 线周期",
            Routes.MARKET_WORKSPACE_PROVIDERS: "选择行情来源",
            Routes.REFERENCE_SELECTED: "已选目录记录",
            Routes.REFERENCE_INSTRUMENT_TYPES: "选择合约类型",
            Routes.OPERATIONS_SERVICES: "项目共享服务",
            Routes.OPERATIONS_INSTANCES: "活动运行实例",
            Routes.OPERATIONS_SUPPORTS: "支撑进程",
            Routes.OPERATIONS_SERVICE: "服务操作",
            Routes.OPERATIONS_OVERVIEW: "运行概览",
            Routes.RESOURCES_SELECTED: "已选运行资源",
            Routes.RESOURCES_MODEL_CHAT: "模型对话",
            Routes.RESOURCES_SETUP: "配置向导",
            Routes.RESOURCES_AI_MODELS: "AI 模型",
            Routes.RESEARCH_DATA: "数据准备",
            Routes.RESEARCH_WORKFLOW: "研究流程",
            Routes.STRATEGY_LAUNCHES: "运行方案",
            Routes.STRATEGY_SELECTED: "已选运行方案",
            Routes.STRATEGY_READINESS: "运行条件",
            Routes.STRATEGY_INSTANCES: "运行实例",
            Routes.STRATEGY_INSTANCE: "已选实例",
            Routes.STRATEGY_COMPONENTS: "实例组件",
            Routes.STRATEGY_ATTACH: "跟随输出",
            Routes.STRATEGY_TIMELINE: "实例时间线",
            Routes.STRATEGY_EXECUTION: "Execution Server",
            Routes.STRATEGY_MARKET: "Market 组件",
            Routes.STRATEGY_SETUP: "配置向导",
        }
        parts.append(labels.get(context, "查询结果"))
        if len(context) == 3 and starts_with(context, Routes.OPERATIONS_SERVICE):
            parts[-1] = service_display_name(context[2])
        elif len(context) == 3 and starts_with(context, Routes.OPERATIONS_SERVICE_LOGS):
            parts[-1] = f"{service_display_name(context[2])} / 实时日志"
        elif len(context) == 3 and starts_with(context, Routes.OPERATIONS_SUPPORT):
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
