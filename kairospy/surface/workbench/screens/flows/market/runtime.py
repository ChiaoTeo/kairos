"""Market interaction flow owned by its Workbench product slice."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from time import time_ns
from typing import Any
from uuid import uuid4

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.pretty import Pretty
from rich.table import Table
from rich.text import Text

from ....widgets import (
    ActionToken,
    ActionItem,
    ChoiceInteraction,
    ConfirmInteraction,
    ControlInteraction,
    Feature,
    InputInteraction,
    renderable_plain_text,
)
from ...activity import ActivityKind, ActivityOutcome, ActivityRecord
from ...effects import (
    AppendActivity,
    RefreshMarketControl,
    RunOperation,
    ScreenEffect,
    SetInteraction,
    SetStatus,
)
from ...navigation.catalog import (
    MARKET_ADVANCED_ACTIONS,
    MarketTask,
    RESUME_MARKET_SEARCH_ACTION,
    SECTION_ACTIONS,
)
from ...presentation import ResultTone, conclusion, count, facts, section
from .actions import (
    MARKET_CONTROL_ACTIONS,
    MarketFilePromptState,
    MarketRouteView,
    diagnostic_command,
    execute_file_action,
    file_command,
    file_result_renderable,
    load_datasets,
    load_observation,
    load_routes,
    observation_command,
    observation_renderable,
    preview_file_action,
    route_diagnostic_renderable,
    route_command,
    run_diagnostic,
    selected_market_actions,
)
from ...session import GuidedSession
from ..reference.actions import (
    CatalogSetupGoal,
    CatalogSetupPlanView,
    catalog_not_initialized,
    catalog_setup_renderable,
    load_catalog_setup_plan,
    load_records,
    prepare_catalog_source,
)
from .workspace import (
    LIVE_MARKET_UNAVAILABLE_ACTIONS,
    SNAPSHOT_KIND_ACTIONS,
    SUBSCRIPTION_CONTENT_ACTIONS,
    TIMEFRAME_ACTIONS,
    LIVE_MARKET_ACTIONS,
    WorkspaceMarketPromptState,
    equivalent_command as workspace_market_command,
    execute as execute_workspace_market,
    live_market_available,
    mutation_renderable as workspace_mutation_renderable,
    provider_options as workspace_provider_options,
    prompt_renderable as workspace_prompt_renderable,
    preview as preview_workspace_market,
    subscriptions_renderable as workspace_subscriptions_renderable,
    unavailable_renderable,
)
from ...navigation import (
    Routes,
    Section,
    action_id,
    belongs_to,
    context_items,
    context_label,
    record_description,
    record_label,
    route,
)
from ...operation import OperationSpec
from ...results import ResultKind, ResultRoute
from ...selection import SelectionRecord, selected_value, selection_records


def handle_input(
    state: Any, session: GuidedSession, token: ActionToken, value: str
) -> tuple[ScreenEffect, ...] | None:
    """Continue one typed Market input interaction."""

    if token.feature is Feature.MARKET:
        if token.action == "search":
            return handle_command(state, session, "market", (value,))
        if token.action == "workspace-market-search":
            return handle_command(state, session, "workspace-market:search", (value,))
        if token.action == "file-field" and token.field is not None:
            return handle_command(
                state, session, f"market-file:field:{token.field}", (value,)
            )
        if token.action == "workspace-field" and token.field is not None:
            return handle_command(
                state, session, f"workspace-market:field:{token.field}", (value,)
            )
    return None


def cancel_input(session: GuidedSession, token: ActionToken) -> bool:
    """Clear only the Market prompt owned by the token."""

    if token.feature is not Feature.MARKET:
        return False
    if token.action == "file-field":
        session.market.file_prompt = None
    elif token.action in {"workspace-field", "workspace-market-search"}:
        session.market.workspace_prompt = None
    return True


def live_observation_operation(
    state: Any, session: GuidedSession
) -> Callable[[], Any] | None:
    """Return the current Market refresh operation for the Textual worker."""

    market = session.market.selected
    observation = session.market.observation
    provider = session.market.provider
    if market is None or observation is None or provider is None:
        return None
    return lambda: load_observation(state, market, observation, provider)


def selected_title(session: GuidedSession) -> str | None:
    market = session.market.selected
    if market is None:
        return None
    values = [record_label(market)]
    exchange_id = getattr(market, "exchange_id", None)
    if exchange_id:
        values.append(str(exchange_id).rsplit(":", 1)[-1])
    instrument_kind = getattr(market, "instrument_kind", None)
    if instrument_kind:
        values.append(str(instrument_kind))
    return " · ".join(values)


def handle_command(
    state: Any,
    session: GuidedSession,
    command: str,
    arguments: tuple[str, ...],
) -> tuple[ScreenEffect, ...] | None:
    """Handle typed input commands owned by Market."""

    if (
        command == "c"
        and not arguments
        and (not session.context or session.context[0] == "market")
    ):
        return _enter_live_market(state, session)

    if command == "market":
        query = " ".join(arguments).strip()
        if not query:
            return _ask_market(session)
        return (_run_market_search(state, session, query),)

    if command.startswith("market-file:field:"):
        prompt = session.market.file_prompt
        if not isinstance(prompt, MarketFilePromptState):
            session.enter_context(Routes.MARKET)
            return _choice(
                state,
                session,
                summary=Text(
                    "Market 文件操作参数向导已经失效，请重新选择操作。",
                    style="yellow",
                ),
                status="参数向导已失效",
            )
        return _accept_prompt_field(
            state,
            session,
            prompt,
            command.removeprefix("market-file:field:"),
            " ".join(arguments),
            workspace=False,
        )

    if command.startswith("workspace-market:field:"):
        prompt = session.market.workspace_prompt
        if not isinstance(prompt, WorkspaceMarketPromptState):
            session.context = Routes.MARKET_LIVE
            return _choice(
                state,
                session,
                summary=Text("Workspace Market 参数向导已经失效。", style="yellow"),
                status="参数向导已失效",
            )
        return _accept_prompt_field(
            state,
            session,
            prompt,
            command.removeprefix("workspace-market:field:"),
            " ".join(arguments),
            workspace=True,
        )
    if command == "workspace-market:search":
        prompt = session.market.workspace_prompt
        if not isinstance(prompt, WorkspaceMarketPromptState):
            session.context = Routes.MARKET_LIVE
            return _choice(
                state,
                session,
                summary=Text("Workspace Market 参数向导已经失效。", style="yellow"),
                status="参数向导已失效",
            )
        query = " ".join(arguments).strip()
        if not query:
            return _ask_workspace_market(session, prompt, error="请输入代码或名称。")
        return (_run_workspace_market_search(state, query),)
    return None


def handle_context(
    state: Any,
    session: GuidedSession,
    command: str,
) -> tuple[ScreenEffect, ...] | None:
    """Advance a Market navigation context."""

    if belongs_to(session.context, Section.MARKET):
        return _handle_market_context(state, session, command)
    return None


def handle_success(
    state: Any,
    session: GuidedSession,
    spec: OperationSpec,
    result: Any,
) -> tuple[ScreenEffect, ...] | None:
    """Interpret result kinds owned by the Market product slice."""

    kind = spec.route.kind
    if kind is ResultKind.MARKET:
        records = tuple(result or ())
        visible = _visible(records)
        if spec.route.qualifier == "workspace-market":
            prompt = session.market.workspace_prompt
            if not isinstance(prompt, WorkspaceMarketPromptState):
                session.context = Routes.MARKET_LIVE
                return _choice(state, session, status="参数向导已失效")
            if not records:
                session.context = Routes.MARKET_LIVE
                return _ask_workspace_market(
                    session,
                    prompt,
                    error="没有找到匹配的市场，请换一个代码或名称。",
                )
            session.market.records = visible
            return _show_record_choices(
                session,
                "market",
                records,
                record_kind="workspace-market-results",
            )
        session.market.records = visible
        if records:
            return _show_record_choices(session, "market", visible)
        session.enter_context(Routes.MARKET_MISSING)
        interaction = ChoiceInteraction(
            title=context_label(session.context, session.root_label),
            summary=Text(
                f"没有找到“{session.market.query or '这个代码'}”。"
                "你可以选择交易所和品种，让项目准备相应目录。",
                style="yellow",
            ),
            actions=context_items(session, state),
        )
        session.interaction = interaction
        return SetInteraction(interaction), SetStatus("没有找到匹配结果")

    if kind is ResultKind.MARKET_CATALOG_SETUP:
        plan = (
            result
            if isinstance(result, CatalogSetupPlanView)
            else CatalogSetupPlanView.from_mapping(result)
            if isinstance(result, Mapping)
            else CatalogSetupPlanView.from_mapping({})
        )
        session.market.catalog_setup_plan = plan
        body = catalog_setup_renderable(plan)
        if session.context != Routes.MARKET_CATALOG_SETUP:
            return (_activity(spec, body),)
        if _catalog_is_usable(plan) and session.market.query:
            session.return_to_context(Routes.MARKET_MISSING)
            return _activity(spec, body), _run_market_search(
                state, session, session.market.query
            )
        session.restore_context(Routes.MARKET_CATALOG_SETUP)
        return (
            _activity(spec, body),
            *_choice(state, session, summary=body, status="准备条件已检查"),
        )

    if kind is ResultKind.MARKET_CATALOG_PREPARE:
        prepared = dict(result) if isinstance(result, Mapping) else {}
        plan = prepared.get("plan")
        if isinstance(plan, CatalogSetupPlanView):
            session.market.catalog_setup_plan = plan
        elif isinstance(plan, Mapping):
            session.market.catalog_setup_plan = CatalogSetupPlanView.from_mapping(plan)
        body = catalog_setup_renderable(
            session.market.catalog_setup_plan or CatalogSetupPlanView.from_mapping({})
        )
        if session.context != Routes.MARKET_CATALOG_SETUP:
            return (_activity(spec, body),)
        if (
            _catalog_is_usable(session.market.catalog_setup_plan)
            and session.market.query
        ):
            session.return_to_context(Routes.MARKET_MISSING)
            return _activity(spec, body), _run_market_search(
                state, session, session.market.query
            )
        session.restore_context(Routes.MARKET_CATALOG_SETUP)
        return (
            _activity(spec, body),
            *_choice(
                state,
                session,
                summary=body,
                status="目录准备已启动 · 可查看进度或返回原搜索",
            ),
        )

    if kind is ResultKind.MARKET_OBSERVATION:
        market = session.market.selected
        if market is None:
            session.enter_context(Routes.MARKET)
            interaction = ChoiceInteraction(
                title=f"{session.root_label} / 市场与标的",
                summary=Text("行情上下文已经失效，请重新选择标的。", style="yellow"),
                actions=SECTION_ACTIONS[Section.MARKET],
            )
            return SetInteraction(interaction), SetStatus("行情上下文已失效")

        body = observation_renderable(result)
        session.market.snapshot = body
        session.restore_context(Routes.MARKET_SELECTED)
        session.visible_records = session.market.records
        actions = context_items(session, state)
        interaction = (
            ControlInteraction(
                title=record_label(market),
                snapshot=body,
                actions=actions,
                refreshing=True,
            )
            if session.market.refresh_enabled
            else ChoiceInteraction(title=record_label(market), actions=actions)
        )
        activity = ActivityRecord(
            activity_id=spec.operation_id,
            kind=ActivityKind.QUERY,
            outcome=ActivityOutcome.SUCCESS,
            title=spec.display_title,
            body=body,
            copy_text=renderable_plain_text(body),
            audit_summary=spec.audit_summary,
            scope_label=spec.scope_label,
        )
        return (
            AppendActivity(activity),
            SetInteraction(interaction),
            SetStatus("行情已就绪"),
        )

    if kind is ResultKind.MARKET_ROUTES:
        routes = _route_views(result)
        session.market.routes = routes
        market = session.market.selected
        observation = session.market.observation or "quote"
        if market is None:
            session.enter_context(Routes.MARKET)
            return _choice(
                state,
                session,
                summary=Text("行情上下文已经失效，请重新选择标的。", style="yellow"),
                status="行情上下文已失效",
            )
        preferred = next(
            (route for route in routes if route.provider == session.market.provider),
            None,
        )
        if not routes:
            body = route_diagnostic_renderable(state, market, observation)
            session.restore_context(Routes.MARKET_SELECTED)
            session.visible_records = session.market.records
            return (
                _activity(spec, body),
                *_choice(state, session, status="没有可用行情数据源"),
            )
        if preferred is not None or len(routes) == 1:
            provider = (preferred or routes[0]).provider
            return (_run_observation(state, session, provider),)
        session.enter_context(Routes.MARKET_PROVIDERS)
        session.visible_records = selection_records(
            routes,
            key=lambda route: route.provider,
            label=lambda route: route.provider or "未知 Provider",
            description=lambda route: route.description,
        )
        return _choice(state, session, status="请选择行情数据源")

    if kind is ResultKind.MARKET_DATASETS:
        body = _market_result("本地行情数据", result)
        return (
            _activity(spec, body, _result_outcome(result)),
            *_choice(state, session, status="数据集已就绪"),
        )

    if kind is ResultKind.MARKET_DIAGNOSTIC:
        session.restore_context(Routes.MARKET_SELECTED)
        session.visible_records = session.market.records
        body = _market_result("Market 诊断", result, diagnostic=True)
        return (
            _activity(spec, body, _result_outcome(result)),
            *_choice(state, session, status="诊断已完成"),
        )

    if kind is ResultKind.MARKET_FILE:
        prompt = session.market.file_prompt
        session.market.file_prompt = None
        session.restore_context(Routes.MARKET_SELECTED)
        session.visible_records = session.market.records
        body = (
            file_result_renderable(result, prompt)
            if isinstance(prompt, MarketFilePromptState)
            else _market_result("Market 文件操作", result)
        )
        artifact_path: Path | None = None
        if isinstance(prompt, MarketFilePromptState) and prompt.action != "replay":
            destination = Path(str(prompt.values.get("destination") or ""))
            candidates = [destination]
            if state.owner is not None and not destination.is_absolute():
                candidates.append(state.owner.paths.root / destination)
            if any(path.is_file() for path in candidates):
                artifact_path = destination
        return (
            _activity(
                spec,
                body,
                _result_outcome(result),
                artifact_path=artifact_path,
            ),
            *_choice(state, session, status="文件操作已完成"),
        )

    if kind is ResultKind.WORKSPACE_MARKET:
        prompt = session.market.workspace_prompt
        if spec.route.qualifier == "provider-options":
            if not isinstance(prompt, WorkspaceMarketPromptState):
                session.context = Routes.MARKET_LIVE
                return _choice(state, session, status="行情来源选择已失效")
            routes = tuple(
                value for value in result or () if isinstance(value, Mapping)
            )
            selected = tuple(
                str(value.get("provider") or "")
                for value in routes
                if value.get("selected") and value.get("provider")
            )
            providers = tuple(
                dict.fromkeys(
                    str(value.get("provider") or "")
                    for value in routes
                    if value.get("provider")
                )
            )
            automatic = (
                selected[0] if selected else providers[0] if len(providers) == 1 else ""
            )
            if automatic:
                prompt.values["provider"] = automatic
                prompt.provider_resolved = True
                return _advance_workspace_prompt(state, session, prompt)
            if not providers:
                session.market.workspace_prompt = None
                session.context = Routes.MARKET_LIVE
                return _choice(
                    state,
                    session,
                    summary=Text(
                        f"{prompt.market_label or '所选市场'} 当前没有可用的行情来源。",
                        style="yellow",
                    ),
                    status="没有可用行情来源",
                )
            actions = tuple(
                ActionItem(
                    f"provider:{provider}",
                    provider,
                    _provider_description(provider, routes),
                    str(index),
                )
                for index, provider in enumerate(providers, 1)
            )
            session.context = Routes.MARKET_WORKSPACE_PROVIDERS
            interaction = ChoiceInteraction(
                title="选择行情来源",
                summary=workspace_prompt_renderable(prompt),
                actions=actions,
            )
            session.interaction = interaction
            return SetInteraction(interaction), SetStatus("请选择行情来源")
        if spec.route.qualifier == "unsubscribe-options":
            if not isinstance(prompt, WorkspaceMarketPromptState):
                session.context = Routes.MARKET_LIVE
                return _choice(state, session, status="退出行情向导已失效")
            subscriptions = tuple(
                value
                for value in (
                    result.get("subscriptions", ())
                    if isinstance(result, Mapping)
                    else ()
                )
                if isinstance(value, Mapping)
            )
            if not subscriptions:
                session.market.workspace_prompt = None
                session.context = Routes.MARKET_LIVE
                session.visible_records = ()
                return _choice(
                    state,
                    session,
                    summary=Text("当前 Kairos I 会话没有可退出的行情。", style="dim"),
                    status="当前会话没有行情订阅",
                )
            visible = selection_records(
                subscriptions,
                key=lambda value: str(value.get("subscription_id") or ""),
                label=lambda value: str(value.get("_market_label") or "已订阅市场"),
                description=lambda value: str(
                    value.get("_market_description") or "当前会话行情"
                ),
            )
            session.visible_records = visible
            session.context = Routes.MARKET_WORKSPACE_SUBSCRIPTIONS
            interaction = ChoiceInteraction(
                title="退出当前会话行情",
                summary=Text("选择要停止接收的行情。", style="dim"),
                actions=context_items(session, state),
            )
            session.interaction = interaction
            return SetInteraction(interaction), SetStatus("请选择要退出的行情")
        session.market.workspace_prompt = None
        session.context = Routes.MARKET_LIVE
        if isinstance(result, Mapping) and result.get("status") == "preview":
            body = _market_result(spec.display_title, result)
        elif (
            isinstance(prompt, WorkspaceMarketPromptState)
            and prompt.action == "snapshot"
            and isinstance(result, Mapping)
        ):
            snapshot = dict(result)
            snapshot.setdefault("data_type", prompt.values["kind"])
            snapshot.setdefault("symbol", prompt.values["market-id"])
            snapshot.setdefault("provider", prompt.values.get("provider") or "自动选择")
            snapshot["_source_mode"] = "workspace-view"
            snapshot["_fetched_at_unix_nanos"] = time_ns()
            body = observation_renderable(snapshot)
        elif (
            isinstance(prompt, WorkspaceMarketPromptState)
            and prompt.action == "session-subscriptions"
            and isinstance(result, Mapping)
        ):
            body = workspace_subscriptions_renderable(
                result,
                current_session=True,
            )
        elif (
            isinstance(prompt, WorkspaceMarketPromptState)
            and prompt.action in {"subscribe", "unsubscribe"}
            and isinstance(result, Mapping)
        ):
            body = workspace_mutation_renderable(result, prompt)
        else:
            body = _market_result("我的实时行情", result)
        return (
            _activity(spec, body, _result_outcome(result)),
            *_choice(state, session, status="操作已完成"),
        )

    return None


def handle_failure(
    state: Any,
    session: GuidedSession,
    spec: OperationSpec,
    error: str,
) -> tuple[ScreenEffect, ...] | None:
    """Restore this product's interaction after a failed finite operation."""

    if spec.route.kind is ResultKind.MARKET:
        if spec.route.qualifier == "workspace-market":
            prompt = session.market.workspace_prompt
            if isinstance(prompt, WorkspaceMarketPromptState):
                effects = _ask_workspace_market(session, prompt, error=error)
                return (
                    _activity(spec, Text(error, style="red"), ActivityOutcome.FAILURE),
                    *effects,
                )
        if catalog_not_initialized(error):
            session.enter_context(Routes.MARKET_MISSING)
            message = Text(
                "这个项目还没有可查询的标的目录。"
                "你可以选择交易所和品种，Kairos 会推荐合适的数据服务并带你完成准备。",
                style="yellow",
            )
            interaction = ChoiceInteraction(
                title=context_label(session.context, session.root_label),
                summary=message,
                actions=context_items(session, state),
            )
            session.interaction = interaction
            return (
                _activity(spec, message, ActivityOutcome.FAILURE),
                SetInteraction(interaction),
                SetStatus("标的目录尚未准备"),
            )
        _ask_market(session)
        interaction = session.interaction
        assert isinstance(interaction, InputInteraction)
        interaction = InputInteraction(
            action=interaction.action,
            title=interaction.title,
            prompt=interaction.prompt,
            detail=interaction.detail,
            value_summary=interaction.value_summary,
            secret=interaction.secret,
            error=error,
        )
        session.interaction = interaction
        return (
            _activity(spec, Text(error, style="red"), ActivityOutcome.FAILURE),
            SetInteraction(interaction),
            SetStatus("搜索市场失败 · 请重试"),
        )
    if spec.route.kind not in _RESULT_KINDS:
        return None
    session.clear_result_flow(spec.route.kind)
    return (
        _activity(spec, Text(error, style="red"), ActivityOutcome.FAILURE),
        *_choice(
            state,
            session,
            status="操作失败 · 可重试、返回或查看帮助",
        ),
    )


def handle_cancel(
    state: Any,
    session: GuidedSession,
    spec: OperationSpec,
) -> tuple[ScreenEffect, ...] | None:
    """Restore this product's navigation after cancellation."""

    if spec.route.kind not in _RESULT_KINDS:
        return None
    workspace_market_search = (
        spec.route.kind is ResultKind.MARKET
        and spec.route.qualifier == "workspace-market"
    )
    session.clear_result_flow(spec.route.kind)
    if workspace_market_search:
        session.market.workspace_prompt = None
        session.enter_context(Routes.MARKET_LIVE)
    elif spec.route.kind is ResultKind.MARKET:
        session.enter_context(Routes.MARKET)
    body = Text("操作在开始执行后被取消。", style="yellow")
    return (
        _activity(spec, body, ActivityOutcome.CANCELLED),
        *_choice(state, session, status="操作已取消 · 可继续输入"),
    )


_RESULT_KINDS = frozenset(
    {
        ResultKind.MARKET,
        ResultKind.MARKET_ROUTES,
        ResultKind.MARKET_OBSERVATION,
        ResultKind.MARKET_DATASETS,
        ResultKind.MARKET_FILE,
        ResultKind.WORKSPACE_MARKET,
        ResultKind.MARKET_DIAGNOSTIC,
        ResultKind.MARKET_CATALOG_SETUP,
        ResultKind.MARKET_CATALOG_PREPARE,
    }
)


def _handle_market_context(
    state: Any, session: GuidedSession, command: str
) -> tuple[ScreenEffect, ...] | None:
    if session.context == Routes.MARKET_MISSING:
        action = action_id(context_items(session, state), command)
        if action == "prepare":
            session.market.catalog_setup_goal = None
            session.market.catalog_setup_plan = None
            session.enter_context(Routes.MARKET_CATALOG_EXCHANGE)
            return _choice(state, session, status="请选择要准备的市场或交易服务")
        if action == "retry" and session.market.query:
            return (_run_market_search(state, session, session.market.query),)
        if action == "catalog":
            session.enter_context(Routes.REFERENCE)
            return _choice(state, session, status="请选择要浏览的标的目录")
        return None

    if session.context == Routes.MARKET_CATALOG_EXCHANGE:
        exchange = action_id(context_items(session, state), command)
        if exchange is None:
            return None
        if exchange == "provider:binance-equity":
            session.market.catalog_setup_goal = CatalogSetupGoal.provider_product(
                "binance", "equity"
            )
            session.enter_context(Routes.MARKET_CATALOG_SETUP)
            return (_run_catalog_setup_plan(state, session),)
        session.market.catalog_setup_goal = CatalogSetupGoal.exchange(exchange)
        session.enter_context(Routes.MARKET_CATALOG_INSTRUMENT)
        return _choice(state, session, status="请选择要准备的品种")

    if session.context == Routes.MARKET_CATALOG_INSTRUMENT:
        instrument_kind = action_id(context_items(session, state), command)
        goal = session.market.catalog_setup_goal
        if instrument_kind is None or goal is None:
            return None
        session.market.catalog_setup_goal = CatalogSetupGoal.exchange(
            goal.exchange_id or "", instrument_kind
        )
        session.enter_context(Routes.MARKET_CATALOG_SETUP)
        return (_run_catalog_setup_plan(state, session),)

    if session.context == Routes.MARKET_CATALOG_SETUP:
        action = action_id(context_items(session, state), command)
        if action == "check":
            return (_run_catalog_setup_plan(state, session),)
        if action == "change":
            session.enter_context(Routes.MARKET_CATALOG_EXCHANGE)
            return _choice(state, session, status="请重新选择市场或交易服务")
        if action == "search-again" and session.market.query:
            return (_run_market_search(state, session, session.market.query),)
        if action == "configure-connection":
            return _start_catalog_connection_setup(state, session)
        if action == "start":
            return _confirm_catalog_preparation(state, session)
        return None

    if session.context == Routes.MARKET_LIVE_UNAVAILABLE:
        action = action_id(LIVE_MARKET_UNAVAILABLE_ACTIONS, command)
        if action is None:
            return None
        if action == "prepare":
            from ..operations.actions import execute_service

            def operation() -> Any:
                result = execute_service(state, "market", "start")
                refresh = getattr(state, "refresh_snapshot", None)
                if callable(refresh):
                    refresh()
                return result

            session.operations.selected_service = "market"
            spec = _spec(
                action_name="operations.market.prepare-live",
                summary="启动项目共享 Market 并返回我的实时行情",
                display_title="准备实时行情",
                route=ResultRoute(ResultKind.OPERATIONS, "live-market-recovery"),
                operation=operation,
                status="正在启动项目共享行情服务…",
            )
            return _confirm_or_run(
                state,
                session,
                spec,
                title="确认启动实时行情",
                details=unavailable_renderable(),
                dangerous=True,
            )
        if action == "service-details":
            from ..operations.runtime import enter_service_detail

            return enter_service_detail(state, session, "market")
        session.enter_context(Routes.MARKET)
        return _choice(
            state,
            session,
            status="请选择历史行情操作" if action == "history" else "已返回市场入口",
        )

    prompt = session.market.workspace_prompt
    if session.context == Routes.MARKET_WORKSPACE_MARKET_RESULTS:
        if not isinstance(prompt, WorkspaceMarketPromptState):
            session.context = Routes.MARKET_LIVE
            return _choice(state, session, status="参数向导已失效")
        record = _record_choice(session.visible_records, command)
        if record is None:
            return None
        prompt.select_market(
            str(getattr(record, "id", "")),
            label=record_label(record),
            description=record_description(record),
        )
        session.market.selected = record
        session.context = Routes.MARKET_LIVE
        session.visible_records = ()
        return _advance_workspace_prompt(state, session, prompt)

    if session.context == Routes.MARKET_WORKSPACE_SUBSCRIPTIONS:
        if not isinstance(prompt, WorkspaceMarketPromptState):
            session.context = Routes.MARKET_LIVE
            return _choice(state, session, status="退出行情向导已失效")
        record = _record_choice(session.visible_records, command)
        if not isinstance(record, Mapping):
            return None
        prompt.select_subscription(
            str(record.get("subscription_id") or ""),
            label=str(record.get("_market_label") or "已订阅市场"),
            description=str(record.get("_market_description") or "当前会话行情"),
        )
        session.context = Routes.MARKET_LIVE
        session.visible_records = ()
        return _advance_workspace_prompt(state, session, prompt)

    if session.context == Routes.MARKET_WORKSPACE_SUBSCRIPTION_CONTENT:
        if not isinstance(prompt, WorkspaceMarketPromptState):
            session.context = Routes.MARKET_LIVE
            return _choice(state, session, status="参数向导已失效")
        observations = action_id(SUBSCRIPTION_CONTENT_ACTIONS, command)
        if observations is None:
            return None
        prompt.accept("observations", observations)
        session.context = Routes.MARKET_LIVE
        return _advance_workspace_prompt(state, session, prompt)

    if session.context == Routes.MARKET_WORKSPACE_SNAPSHOT_KIND:
        if not isinstance(prompt, WorkspaceMarketPromptState):
            session.context = Routes.MARKET_LIVE
            return _choice(state, session, status="参数向导已失效")
        kind = action_id(SNAPSHOT_KIND_ACTIONS, command)
        if kind is None:
            return None
        prompt.accept("kind", kind)
        session.context = Routes.MARKET_LIVE
        return _advance_workspace_prompt(state, session, prompt)

    if session.context == Routes.MARKET_WORKSPACE_TIMEFRAME:
        if not isinstance(prompt, WorkspaceMarketPromptState):
            session.context = Routes.MARKET_LIVE
            return _choice(state, session, status="参数向导已失效")
        timeframe = action_id(TIMEFRAME_ACTIONS, command)
        if timeframe is None:
            return None
        prompt.accept("timeframe", timeframe)
        session.context = Routes.MARKET_LIVE
        return _advance_workspace_prompt(state, session, prompt)

    if session.context == Routes.MARKET_WORKSPACE_PROVIDERS:
        if not isinstance(prompt, WorkspaceMarketPromptState):
            session.context = Routes.MARKET_LIVE
            return _choice(state, session, status="行情来源选择已失效")
        interaction = session.interaction
        if not isinstance(interaction, ChoiceInteraction):
            return None
        provider_action = action_id(interaction.actions, command)
        if provider_action is None or not provider_action.startswith("provider:"):
            return None
        prompt.values["provider"] = provider_action.removeprefix("provider:")
        prompt.provider_resolved = True
        session.context = Routes.MARKET_LIVE
        return _advance_workspace_prompt(state, session, prompt)

    if session.context == Routes.MARKET_SELECTED:
        market = session.market.selected
        if market is None:
            session.enter_context(Routes.MARKET)
            return _choice(state, session, status="行情上下文已失效")
        command = {
            "d": "diagnose",
            "r": "refresh",
            "s": "save-snapshot",
            "w": "watch",
        }.get(command, command)
        actions = context_items(session, state)
        action = action_id(actions, command)
        if action is None:
            return None
        if action == "refresh":
            provider = session.market.provider
            if provider is None:
                return _choice(
                    state,
                    session,
                    summary=Text("尚未选择行情数据源。", style="yellow"),
                    status="无法刷新行情",
                )
            return (_run_observation(state, session, provider),)
        if action == "watch":
            session.market.refresh_enabled = not session.market.refresh_enabled
            interaction = _market_interaction(state, session)
            effects: tuple[ScreenEffect, ...] = (
                SetInteraction(interaction),
                SetStatus(
                    "行情自动刷新中"
                    if session.market.refresh_enabled
                    else "行情自动刷新已暂停"
                ),
            )
            if session.market.refresh_enabled:
                effects = (*effects, RefreshMarketControl())
            return effects
        if action == "save-snapshot":
            snapshot = session.market.snapshot
            if snapshot is None:
                return _choice(
                    state,
                    session,
                    summary=Text("当前没有可保存的行情快照。", style="yellow"),
                    status="没有可保存的行情快照",
                )
            body = snapshot
            summary = f"保存行情快照 · {record_label(market)}"
            return (
                AppendActivity(
                    ActivityRecord(
                        activity_id=str(uuid4()),
                        kind=ActivityKind.QUERY,
                        outcome=ActivityOutcome.SUCCESS,
                        title=summary,
                        body=body,
                        copy_text=renderable_plain_text(body),
                        audit_summary=summary,
                    )
                ),
                SetInteraction(_market_interaction(state, session)),
                SetStatus("当前行情快照已保存到活动历史"),
            )
        if action == "diagnose":
            return (_run_market_diagnostic(state, session),)
        session.market.observation = action
        session.market.snapshot = None
        session.market.refresh_enabled = False
        return (
            RunOperation(
                _spec(
                    action_name=f"market.observe.{action}",
                    summary=f"{record_label(market)} · {action}",
                    route=ResultRoute(ResultKind.MARKET_ROUTES),
                    operation=lambda: load_routes(state, market, action),
                    status="正在查找行情数据源…",
                    equivalent_command=route_command(state, market, action),
                )
            ),
        )

    if session.context == Routes.MARKET_LIVE:
        action = action_id(LIVE_MARKET_ACTIONS, command)
        if action is None:
            return None
        custom_content = action == "subscribe-custom"
        if custom_content:
            action = "subscribe"
        selected_market = session.market.selected
        default_market = (
            str(getattr(selected_market, "id", ""))
            if selected_market is not None
            else ""
        )
        prompt = WorkspaceMarketPromptState(
            action,
            default_market,
            session.market.operator_owner_id,
            custom_content=custom_content,
        )
        if selected_market is not None and default_market:
            prompt.select_market(
                default_market,
                label=record_label(selected_market),
                description=record_description(selected_market),
            )
        session.market.workspace_prompt = prompt
        return _advance_workspace_prompt(state, session, prompt)

    if len(session.context) > 1 and session.visible_records:
        record = _record_choice(session.visible_records, command)
        if record is None:
            return None
        if session.context[1] == "providers":
            provider = record.provider if isinstance(record, MarketRouteView) else ""
            if not provider:
                return _choice(
                    state,
                    session,
                    summary=Text("所选数据源没有 Provider 标识。", style="yellow"),
                    status="数据源无效",
                )
            return (_run_observation(state, session, provider),)
        session.market.selected = record
        session.market.reset_control()
        session.enter_context(Routes.MARKET_SELECTED)
        if session.market.purpose in {"download", "replay"}:
            prompt = MarketFilePromptState(session.market.purpose, record)
            session.market.file_prompt = prompt
            return _advance_file_prompt(state, session, prompt)
        if session.market.purpose == "diagnostics":
            return (_run_market_diagnostic(state, session),)
        return _choice(
            state,
            session,
            status=f"已选择 {record_label(record)} · 请选择行情",
        )

    root_actions = (*SECTION_ACTIONS[Section.MARKET], *MARKET_ADVANCED_ACTIONS)
    if _catalog_is_usable(session.market.catalog_setup_plan) and session.market.query:
        root_actions = (RESUME_MARKET_SEARCH_ACTION, *root_actions)
    action = action_id(root_actions, command)
    if action is None:
        return None
    if action == "resume-search" and session.market.query:
        return (_run_market_search(state, session, session.market.query),)
    if action in {
        MarketTask.SEARCH,
        MarketTask.DOWNLOAD,
        "replay",
        "diagnostics",
        "advanced",
    }:
        session.market.purpose = action
        return _ask_market(session)
    if action == MarketTask.DATASETS:
        return (
            RunOperation(
                _spec(
                    action_name="market.datasets",
                    summary="查看本地行情数据集",
                    route=ResultRoute(ResultKind.MARKET_DATASETS),
                    operation=lambda: load_datasets(state),
                    status="正在读取本地行情数据集…",
                )
            ),
        )
    if action == MarketTask.CATALOG:
        session.enter_context(Routes.REFERENCE)
        return _choice(state, session, status="请选择标的目录")
    if action == MarketTask.LIVE:
        return _enter_live_market(state, session)
    return None


def _catalog_is_usable(plan: CatalogSetupPlanView | None) -> bool:
    return plan is not None and plan.availability in {"usable", "partially_usable"}


def _enter_live_market(state: Any, session: GuidedSession) -> tuple[ScreenEffect, ...]:
    available = live_market_available(state)
    session.enter_context(
        Routes.MARKET_LIVE if available else Routes.MARKET_LIVE_UNAVAILABLE
    )
    return _choice(
        state,
        session,
        summary=None if available else unavailable_renderable(),
        status="我的实时行情" if available else "实时行情暂不可用",
    )


def _ask_market(session: GuidedSession) -> tuple[ScreenEffect, ...]:
    prompt, detail = _market_prompt_copy(session.market.purpose)
    title = {
        "search": "搜索市场",
        "download": "选择历史行情标的",
        "replay": "选择回放标的",
        "diagnostics": "选择待诊断标的",
        "advanced": "高级市场标识",
    }.get(session.market.purpose, "搜索市场")
    session.ask(
        ActionToken(Feature.MARKET, "search"),
        title=title,
        prompt=prompt,
        detail=detail,
    )
    return SetInteraction(session.interaction), SetStatus(f"{title} · 等待输入")


def _market_prompt_copy(purpose: str) -> tuple[str, str]:
    return {
        "search": ("输入代码或名称", "例如 AAPL、比特币或 BTCUSDT。"),
        "download": ("输入要下载的标的", "可输入代码、名称或完整 Market ID。"),
        "replay": ("输入要回放的标的", "可输入代码、名称或完整 Market ID。"),
        "diagnostics": ("输入要诊断的标的", "可输入代码、名称或完整 Market ID。"),
        "advanced": ("输入完整 Market ID", "使用高级市场标识精确定位标的。"),
    }.get(purpose, ("输入代码或名称", "例如 AAPL、比特币或 BTCUSDT。"))


def _run_market_search(state: Any, session: GuidedSession, query: str) -> RunOperation:
    session.market.query = query
    return RunOperation(
        _spec(
            action_name="market.find",
            summary=f"搜索市场标的 · {query}",
            route=ResultRoute(ResultKind.MARKET),
            operation=lambda: load_records(state, "markets", query),
            status="正在搜索市场标的…",
        )
    )


def _run_catalog_setup_plan(state: Any, session: GuidedSession) -> RunOperation:
    goal = session.market.catalog_setup_goal
    if goal is None:
        raise RuntimeError("标的目录准备目标已经失效")
    return RunOperation(
        _spec(
            action_name="market.catalog.check",
            summary="检查标的目录准备条件",
            route=ResultRoute(ResultKind.MARKET_CATALOG_SETUP),
            operation=lambda: load_catalog_setup_plan(state, goal),
            status="正在检查可用来源和账号要求…",
        )
    )


def _start_catalog_connection_setup(
    state: Any, session: GuidedSession
) -> tuple[ScreenEffect, ...]:
    plan = session.market.catalog_setup_plan
    options = plan.options if plan is not None else ()
    selected = plan.recommended_option or 0 if plan is not None else 0
    option = options[selected] if selected < len(options) else None
    provider = option.binding.provider if option is not None else ""
    source = option.binding.source.replace("_", "-") if option is not None else ""
    if provider not in {"massive", "binance", "okx"}:
        return _choice(
            state,
            session,
            summary=Text("该来源目前不需要或不支持账号配置。", style="yellow"),
            status="无需配置账号",
        )

    from ..resources.configuration import _start_wizard
    from ..resources.wizard import ResourceWizardState

    product = {
        ("massive", "equity"): "equity",
        ("massive", "options"): "options",
        ("binance", "usd-m-futures"): "usd-m-futures",
        ("binance", "coin-m-futures"): "coin-m-futures",
        ("okx", "perpetual"): "swap",
    }.get((provider, source), source)
    wizard = ResourceWizardState("data")
    wizard.answers.update(
        {
            "data-provider": provider,
            "data-product": product,
        }
    )
    session.resources.return_context = Routes.MARKET_CATALOG_SETUP
    session.enter_context(Routes.RESOURCES_DATA)
    return _start_wizard(state, session, wizard)


def _confirm_catalog_preparation(
    state: Any, session: GuidedSession
) -> tuple[ScreenEffect, ...]:
    plan = session.market.catalog_setup_plan
    if plan is None:
        return (_run_catalog_setup_plan(state, session),)
    credential_binding = plan.credential_binding

    def operation() -> Any:
        if state.dry_run or state.no_exec:
            return {"status": "preview", "plan": plan}
        return prepare_catalog_source(
            state,
            plan,
            credential_binding=(
                str(credential_binding) if credential_binding else None
            ),
        )

    spec = _spec(
        action_name="market.catalog.prepare",
        summary=f"准备标的目录 · {session.market.query or '当前搜索'}",
        route=ResultRoute(ResultKind.MARKET_CATALOG_PREPARE),
        operation=operation,
        status="正在启动目录准备…",
    )
    return _confirm_or_run(
        state,
        session,
        spec,
        title="确认准备标的目录",
        details=catalog_setup_renderable(plan),
        dangerous=True,
    )


def _run_workspace_market_search(state: Any, query: str) -> RunOperation:
    return RunOperation(
        _spec(
            action_name="workspace.market.find",
            summary=f"选择 Workspace Market 标的 · {query}",
            route=ResultRoute(ResultKind.MARKET, "workspace-market"),
            operation=lambda: load_records(state, "markets", query),
            status="正在查找可用市场…",
        )
    )


def _run_observation(state: Any, session: GuidedSession, provider: str) -> RunOperation:
    market = session.market.selected
    observation = session.market.observation
    assert market is not None and observation is not None
    session.market.provider = provider
    return RunOperation(
        _spec(
            action_name=f"market.observe.{observation}",
            summary=f"{record_label(market)} · {observation} · {provider}",
            route=ResultRoute(ResultKind.MARKET_OBSERVATION),
            operation=lambda: load_observation(state, market, observation, provider),
            status=f"正在通过 {provider} 读取行情…",
            equivalent_command=observation_command(
                state, market, observation, provider
            ),
        )
    )


def _run_market_diagnostic(state: Any, session: GuidedSession) -> RunOperation:
    market = session.market.selected
    assert market is not None
    return RunOperation(
        _spec(
            action_name="market.diagnose",
            summary=f"{record_label(market)} · 市场诊断",
            route=ResultRoute(ResultKind.MARKET_DIAGNOSTIC),
            operation=lambda: run_diagnostic(state, market),
            status="正在诊断当前市场…",
            equivalent_command=diagnostic_command(state, market),
        )
    )


def _accept_prompt_field(
    state: Any,
    session: GuidedSession,
    prompt: MarketFilePromptState | WorkspaceMarketPromptState,
    field_name: str,
    raw: str,
    *,
    workspace: bool,
) -> tuple[ScreenEffect, ...]:
    try:
        prompt.accept(field_name, raw)
    except ValueError as error:
        current = session.interaction
        if isinstance(current, InputInteraction):
            interaction = InputInteraction(
                action=current.action,
                title=current.title,
                prompt=current.prompt,
                detail=current.detail,
                value_summary=current.value_summary,
                secret=current.secret,
                error=str(error),
            )
            session.interaction = interaction
            return SetInteraction(interaction), SetStatus("输入有误 · 请修正")
        return _choice(
            state, session, summary=Text(str(error), style="yellow"), status="输入有误"
        )
    if workspace:
        assert isinstance(prompt, WorkspaceMarketPromptState)
        return _advance_workspace_prompt(state, session, prompt)
    assert isinstance(prompt, MarketFilePromptState)
    return _advance_file_prompt(state, session, prompt)


def _advance_file_prompt(
    state: Any, session: GuidedSession, prompt: MarketFilePromptState
) -> tuple[ScreenEffect, ...]:
    next_prompt = prompt.next_prompt()
    if next_prompt is not None:
        name, label, detail = next_prompt
        session.ask(
            ActionToken(Feature.MARKET, "file-field", name),
            title="Market 文件操作",
            prompt=label,
            detail=detail,
            value_summary=Pretty(prompt.summary(), expand_all=True),
        )
        return SetInteraction(session.interaction), SetStatus("等待 Market 文件参数")

    def operation() -> Any:
        if state.dry_run or state.no_exec:
            return preview_file_action(prompt)
        return execute_file_action(state, prompt)

    spec = _spec(
        action_name=f"market.file.{prompt.action}",
        summary=f"Market {prompt.action} {prompt.market.id}",
        route=ResultRoute(ResultKind.MARKET_FILE),
        operation=operation,
        status=f"正在执行 Market {prompt.action}…",
        equivalent_command=file_command(state, prompt),
    )
    return _confirm_or_run(
        state,
        session,
        spec,
        title="Market 文件操作范围",
        details=Pretty(prompt.summary(), expand_all=True),
        dangerous=True,
    )


def _advance_workspace_prompt(
    state: Any, session: GuidedSession, prompt: WorkspaceMarketPromptState
) -> tuple[ScreenEffect, ...]:
    next_prompt = prompt.next_prompt()
    if next_prompt is not None:
        name, label, detail = next_prompt
        if name == "market-id":
            return _ask_workspace_market(session, prompt)
        if name == "subscription-id":
            return (_run_unsubscribe_options(state, prompt),)
        choice_context: tuple[str, tuple[ActionItem, ...], str] | None = None
        if name == "observations":
            choice_context = (
                "workspace-subscription-content",
                SUBSCRIPTION_CONTENT_ACTIONS,
                "选择要接收的行情内容",
            )
        elif name == "kind":
            choice_context = (
                "workspace-snapshot-kind",
                SNAPSHOT_KIND_ACTIONS,
                "选择要查看的行情内容",
            )
        elif name == "timeframe":
            choice_context = (
                "workspace-timeframe",
                TIMEFRAME_ACTIONS,
                "选择 K 线周期",
            )
        if choice_context is not None:
            context, actions, title = choice_context
            session.context = route(Section.MARKET, context)
            interaction = ChoiceInteraction(
                title=title,
                summary=workspace_prompt_renderable(prompt),
                actions=actions,
            )
            session.interaction = interaction
            return SetInteraction(interaction), SetStatus(title)
        session.ask(
            ActionToken(Feature.MARKET, "workspace-field", name),
            title="我的实时行情",
            prompt=label,
            detail=detail,
            value_summary=workspace_prompt_renderable(prompt),
        )
        return SetInteraction(session.interaction), SetStatus("等待实时行情参数")

    if prompt.action in {"snapshot", "freshness"} and not prompt.provider_resolved:
        if state.dry_run or state.no_exec:
            prompt.provider_resolved = True
            prompt.values.setdefault("provider", "")
            return _advance_workspace_prompt(state, session, prompt)
        return (_run_provider_options(state, prompt),)

    def operation() -> Any:
        if state.dry_run or state.no_exec:
            return preview_workspace_market(prompt)
        return execute_workspace_market(state, prompt)

    spec = _spec(
        action_name=f"workspace.market.{prompt.action}",
        summary=f"Workspace Market {prompt.action}",
        display_title=_workspace_display_title(prompt),
        route=ResultRoute(ResultKind.WORKSPACE_MARKET),
        operation=operation,
        status=f"正在执行 Workspace Market {prompt.action}…",
        equivalent_command=workspace_market_command(state, prompt),
    )
    return _confirm_or_run(
        state,
        session,
        spec,
        title="实时行情操作确认",
        details=workspace_prompt_renderable(prompt),
        dangerous=prompt.dangerous,
    )


def _ask_workspace_market(
    session: GuidedSession,
    prompt: WorkspaceMarketPromptState,
    *,
    error: str | None = None,
) -> tuple[ScreenEffect, ...]:
    session.context = Routes.MARKET_LIVE
    interaction = InputInteraction(
        action=ActionToken(Feature.MARKET, "workspace-market-search"),
        title="选择市场",
        prompt="输入代码或名称",
        detail="例如 AAPL、比特币或 BTCUSDT；输入 /back 取消。",
        value_summary=workspace_prompt_renderable(prompt),
        error=error,
    )
    session.interaction = interaction
    return SetInteraction(interaction), SetStatus("等待市场搜索条件")


def _run_unsubscribe_options(
    state: Any, prompt: WorkspaceMarketPromptState
) -> RunOperation:
    return RunOperation(
        _spec(
            action_name="workspace.market.unsubscribe.options",
            summary="读取当前 Kairos I 行情",
            route=ResultRoute(ResultKind.WORKSPACE_MARKET, "unsubscribe-options"),
            operation=lambda: _load_operator_subscription_options(state, prompt),
            status="正在读取当前会话行情…",
        )
    )


def _run_provider_options(
    state: Any, prompt: WorkspaceMarketPromptState
) -> RunOperation:
    return RunOperation(
        _spec(
            action_name=f"workspace.market.{prompt.action}.providers",
            summary=f"为 {prompt.market_label or '所选市场'} 选择行情来源",
            route=ResultRoute(ResultKind.WORKSPACE_MARKET, "provider-options"),
            operation=lambda: workspace_provider_options(state, prompt),
            status="正在查找可用行情来源…",
        )
    )


def _load_operator_subscription_options(
    state: Any, prompt: WorkspaceMarketPromptState
) -> dict[str, Any]:
    query = WorkspaceMarketPromptState(
        "session-subscriptions", owner_id=prompt.owner_id
    )
    result = execute_workspace_market(state, query)
    subscriptions: list[dict[str, Any]] = []
    for raw in result.get("subscriptions", ()):
        if not isinstance(raw, Mapping):
            continue
        value = dict(raw)
        market_ids = tuple(str(item) for item in raw.get("market_ids", ()) if item)
        market_id = market_ids[0] if market_ids else ""
        try:
            records = load_records(state, "markets", market_id) if market_id else ()
        except (OSError, RuntimeError, ValueError):
            # Reference enriches the label but does not own the user's ability
            # to release a Market-owned subscription.
            records = ()
        exact = next(
            (
                record
                for record in records
                if str(getattr(record, "id", "")) == market_id
            ),
            records[0] if records else None,
        )
        if exact is not None:
            label = record_label(exact)
            market_description = record_description(exact)
        else:
            parts = tuple(part for part in market_id.split(":") if part)
            label = parts[-1] if parts else "未知市场"
            market_description = " · ".join(parts[1:-1]) or "市场目录中已不可用"
        observations = tuple(str(item) for item in raw.get("observations", ()) if item)
        providers = tuple(
            str(item) for item in raw.get("selected_providers", ()) if item
        )
        details = [
            market_description,
            _observation_description(observations),
            "、".join(providers) if providers else "数据来源自动选择",
        ]
        value["_market_label"] = label
        value["_market_description"] = " · ".join(item for item in details if item)
        subscriptions.append(value)
    return {"subscriptions": subscriptions}


def _observation_description(observations: Sequence[str]) -> str:
    labels = {
        "quote": "实时报价",
        "trade": "逐笔成交",
        "bar:1m": "1 分钟 K 线",
        "greeks": "期权 Greeks",
    }
    return "、".join(labels.get(value, value) for value in observations) or "行情"


def _provider_description(provider: str, routes: Sequence[Mapping[str, Any]]) -> str:
    matching = tuple(
        value for value in routes if str(value.get("provider") or "") == provider
    )
    observations = tuple(
        dict.fromkeys(
            str(observation)
            for value in matching
            for observation in value.get("observation_kinds", ())
        )
    )
    content = _observation_description(observations)
    return f"{content} · 可用"


def _confirm_or_run(
    state: Any,
    session: GuidedSession,
    spec: OperationSpec,
    *,
    title: str,
    details: Any,
    dangerous: bool,
) -> tuple[ScreenEffect, ...]:
    if not dangerous or state.yes or state.dry_run or state.no_exec:
        return (RunOperation(spec),)
    session.confirm(spec, title=title, display_summary=details)
    return SetInteraction(session.interaction), SetStatus("等待确认")


def _choice(
    state: Any,
    session: GuidedSession,
    *,
    summary: Any | None = None,
    status: str = "就绪",
) -> tuple[ScreenEffect, ...]:
    interaction = _market_interaction(state, session, summary=summary)
    session.interaction = interaction
    return SetInteraction(interaction), SetStatus(status)


def _market_interaction(
    state: Any, session: GuidedSession, *, summary: Any | None = None
) -> ChoiceInteraction | ControlInteraction:
    if (
        session.context == Routes.MARKET_SELECTED
        and session.market.snapshot is not None
        and session.market.refresh_enabled
        and session.market.selected is not None
    ):
        return ControlInteraction(
            title=record_label(session.market.selected),
            snapshot=session.market.snapshot,
            actions=context_items(session, state),
            refreshing=True,
        )
    return ChoiceInteraction(
        title=context_label(session.context, session.root_label),
        summary=summary,
        actions=context_items(session, state),
    )


def _record_choice(records: tuple[SelectionRecord, ...], value: str) -> object | None:
    return selected_value(records, value)


def _market_result(
    title: str, result: Any, *, diagnostic: bool = False
) -> RenderableType:
    labels = {
        "status": "状态",
        "state": "运行状态",
        "market_id": "Market ID",
        "instrument_id": "Instrument ID",
        "provider": "Provider",
        "data_type": "数据类型",
        "dataset_id": "Dataset ID",
        "record_count": "记录数",
        "path": "路径",
        "destination": "产物",
        "freshness": "新鲜度",
        "stale": "已过期",
        "complete": "完整",
        "reason": "原因",
        "detail": "说明",
        "error": "错误",
    }
    if isinstance(result, Mapping):
        preview = str(result.get("status") or "").lower() == "preview"
        rows = tuple(
            (label, _market_result_value(result[key]))
            for key, label in labels.items()
            if key in result and result[key] is not None
        )
        return Group(
            conclusion(
                f"{title}预演完成，未执行任何修改" if preview else f"{title}已返回结果",
                tone=(
                    ResultTone.PREVIEW
                    if preview
                    else ResultTone.WARNING
                    if diagnostic and result.get("error")
                    else ResultTone.SUCCESS
                ),
            ),
            facts(rows) if rows else Text("没有更多业务字段", style="dim"),
        )
    if isinstance(result, Sequence) and not isinstance(result, (str, bytes)):
        table = Table("序号", "记录", show_header=True, header_style="bold")
        for index, item in enumerate(result[:20], 1):
            table.add_row(str(index), _market_result_value(item))
        return Group(
            conclusion(f"{title}共 {count(len(result))} 条记录"),
            section("结果", table),
            Text(
                f"显示 {count(min(len(result), 20))} 条 · 其余 {count(max(len(result) - 20, 0))} 条",
                style="dim",
            ),
        )
    return conclusion(str(result) or f"{title}已完成")


def _market_result_value(value: Any) -> str:
    if isinstance(value, Mapping):
        identity_value = (
            value.get("market_id") or value.get("dataset_id") or value.get("provider")
        )
        return str(identity_value or "结构化记录")
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return f"{count(len(value))} 项"
    return str(value)


def _result_outcome(result: Any) -> ActivityOutcome:
    if isinstance(result, Mapping) and result.get("status") == "preview":
        return ActivityOutcome.ATTENTION
    return ActivityOutcome.SUCCESS


def _spec(
    *,
    action_name: str,
    summary: str,
    display_title: str | None = None,
    route: ResultRoute,
    operation: Any,
    status: str,
    equivalent_command: tuple[str, ...] | None = None,
) -> OperationSpec:
    return OperationSpec.create(
        action_name=action_name,
        audit_summary=summary,
        display_title=display_title,
        route=route,
        operation=operation,
        running_status=status,
        equivalent_command=equivalent_command,
    )


def _workspace_display_title(prompt: WorkspaceMarketPromptState) -> str:
    titles = {
        "session-subscriptions": "我的实时行情",
        "subscribe": "添加实时行情",
        "unsubscribe": "停止关注实时行情",
        "snapshot": "查看实时行情快照",
        "freshness": "查看行情新鲜度",
    }
    return titles.get(prompt.action, "实时行情操作结果")


def _activity(
    spec: OperationSpec,
    body: Any,
    outcome: ActivityOutcome = ActivityOutcome.SUCCESS,
    *,
    artifact_path: Path | None = None,
) -> AppendActivity:
    return AppendActivity(
        ActivityRecord(
            activity_id=spec.operation_id,
            kind=ActivityKind.QUERY,
            outcome=outcome,
            title=spec.display_title,
            body=body,
            copy_text=renderable_plain_text(body),
            audit_summary=spec.audit_summary,
            scope_label=spec.scope_label,
            artifact_path=artifact_path,
        )
    )


def _standalone_activity(title: str, body: Any) -> AppendActivity:
    return AppendActivity(
        ActivityRecord(
            activity_id=f"activity-{uuid4().hex[:12]}",
            kind=ActivityKind.QUERY,
            outcome=ActivityOutcome.SUCCESS,
            title=title,
            body=body,
            copy_text=renderable_plain_text(body),
            audit_summary=title,
        )
    )


def _show_record_choices(
    session: GuidedSession,
    section: str,
    records: tuple[Any, ...],
    *,
    record_kind: str | None = None,
) -> tuple[ScreenEffect, ...]:
    session.enter(section, record_kind or "results")
    visible = _visible(records)
    session.visible_records = visible
    actions = tuple(
        ActionItem(
            str(index),
            record.label,
            record.description,
            str(index),
        )
        for index, record in enumerate(visible, 1)
    )
    interaction = ChoiceInteraction(
        title=(
            f"{session.root_label} / 市场与标的"
            if section == "market"
            else f"{session.root_label} / 市场标的"
        ),
        summary=_market_group_summary(records) if section == "market" else None,
        actions=actions,
    )
    return (
        SetInteraction(interaction),
        SetStatus(f"找到 {len(records)} 个结果 · 请选择"),
    )


def _visible(records: tuple[Any, ...]) -> tuple[SelectionRecord, ...]:
    return selection_records(
        records,
        label=_market_choice_label,
        description=_market_choice_description,
    )


def _market_group_summary(records: tuple[Any, ...]) -> RenderableType:
    groups: dict[str, list[str]] = {}
    for record in records:
        label = _market_group_label(record)
        exchange = _exchange_label(getattr(record, "exchange_id", None))
        exchanges = groups.setdefault(label, [])
        if exchange not in exchanges:
            exchanges.append(exchange)

    lines = Text()
    for label, exchanges in groups.items():
        market_count = sum(
            1 for record in records if _market_group_label(record) == label
        )
        if lines:
            lines.append("\n")
        lines.append(label, style="bold")
        lines.append(f" · {market_count} 个市场 · ", style="dim")
        lines.append("、".join(exchanges))
    return Group(
        Text("按交易品种归类；请选择一个具体市场。", style="dim"),
        lines,
    )


def _market_choice_label(record: Any) -> str:
    return _market_group_label(record)


def _market_choice_description(record: Any) -> str:
    exchange = _exchange_label(getattr(record, "exchange_id", None))
    venue_symbol = str(getattr(record, "venue_symbol", None) or "").strip()
    status = _STATUS_LABELS.get(str(getattr(record, "status", "")), "状态未知")
    values = [exchange]
    if venue_symbol:
        values.append(venue_symbol)
    values.append(status)
    return " · ".join(values)


def _market_group_label(record: Any) -> str:
    kind = str(getattr(record, "instrument_kind", "unknown"))
    kind_label = _INSTRUMENT_KIND_LABELS.get(kind, "其他品种")
    base = _asset_label(getattr(record, "base_asset", None))
    quote = _asset_label(getattr(record, "quote_asset", None))
    if base and quote:
        subject = f"{base}/{quote}"
    else:
        instrument = getattr(record, "instrument", None)
        subject = str(
            getattr(instrument, "display_symbol", None)
            or getattr(record, "venue_symbol", None)
            or "未命名品种"
        )
    return f"{subject} · {kind_label}"


def _asset_label(value: Any) -> str:
    if value is None:
        return ""
    return str(value).rsplit(":", 1)[-1]


def _exchange_label(value: Any) -> str:
    key = str(value or "").rsplit(":", 1)[-1]
    return _EXCHANGE_LABELS.get(key.lower(), key or "未知交易所")


_INSTRUMENT_KIND_LABELS = {
    "equity": "股票",
    "spot": "现货",
    "perpetual": "永续合约",
    "future": "期货",
    "option": "期权",
    "index": "指数",
}

_STATUS_LABELS = {
    "active": "当前有效",
    "trading": "正在交易",
    "inactive": "当前不可用",
    "halted": "暂停交易",
    "delisted": "已退市",
    "unknown": "状态未知",
}

_EXCHANGE_LABELS = {
    "nasdaq": "Nasdaq",
    "nyse": "NYSE",
    "amex": "AMEX",
    "binance": "Binance",
    "okx": "OKX",
    "hyperliquid": "Hyperliquid",
}


def _route_views(result: Any) -> tuple[MarketRouteView, ...]:
    routes: list[MarketRouteView] = []
    for route in result or ():
        if isinstance(route, MarketRouteView):
            routes.append(route)
        elif isinstance(route, Mapping):
            routes.append(MarketRouteView.from_mapping(route))
    return tuple(routes)


__all__ = [
    "handle_cancel",
    "handle_command",
    "handle_context",
    "handle_failure",
    "handle_success",
]
